use crate::{
    domain::{cache_dir, Catalog, Settings},
    network::{Cancellation, HttpStatusError},
    storage::{PaperStore, Repository},
};
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Datelike, Duration, TimeZone, Timelike, Utc};
use fs2::FileExt;
use std::{
    fs::{self, File, OpenOptions},
    path::Path,
};

pub struct DownloadLock {
    _file: File,
}
impl DownloadLock {
    pub fn acquire() -> Result<Self> {
        let directory = cache_dir();
        fs::create_dir_all(&directory)?;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(directory.join("download.lock"))?;
        file.try_lock_exclusive()
            .context("Another Python or Rust download job is running. Wait for it to finish.")?;
        Ok(Self { _file: file })
    }
}
pub fn windows(
    start: DateTime<Utc>,
    mut end: DateTime<Utc>,
) -> Vec<(DateTime<Utc>, DateTime<Utc>)> {
    let mut result = Vec::new();
    while end >= start {
        let first = Utc
            .with_ymd_and_hms(end.year(), end.month(), 1, 0, 0, 0)
            .unwrap();
        result.push((first.max(start), end));
        end = first - Duration::minutes(1);
    }
    result
}
pub struct DownloadService<C: Catalog, R: PaperStore, F: FnMut(String)> {
    pub catalog: C,
    pub repository: R,
    pub stop: Cancellation,
    pub emit: F,
    downloaded: usize,
    skipped: usize,
    count: usize,
}
impl<C: Catalog, R: PaperStore, F: FnMut(String)> DownloadService<C, R, F> {
    pub fn new(catalog: C, repository: R, stop: Cancellation, emit: F) -> Self {
        Self {
            catalog,
            repository,
            stop,
            emit,
            downloaded: 0,
            skipped: 0,
            count: 0,
        }
    }
    fn window(
        &mut self,
        category: &str,
        folder: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        limit: Option<usize>,
    ) -> Result<()> {
        self.stop.check()?;
        if limit.is_some_and(|value| self.count >= value) {
            return Ok(());
        }
        let (total, mut page) = self.catalog.page(category, start, end, 0)?;
        if total > 1000 {
            let minutes = (end - start).num_minutes();
            if minutes == 0 {
                bail!("Too many results in one minute. Use arXiv bulk access.");
            }
            let middle = start + Duration::minutes(minutes / 2);
            self.window(category, folder, middle + Duration::minutes(1), end, limit)?;
            self.window(category, folder, start, middle, limit)?;
            return Ok(());
        }
        let mut offset = 0;
        while offset < total {
            if page.is_empty() {
                bail!("arXiv returned an incomplete page. Retry the job later.");
            }
            let length = page.len();
            for paper in &page {
                self.stop.check()?;
                if self.repository.contains(paper)? {
                    self.skipped += 1;
                } else {
                    (self.emit)(format!(
                        "Downloading {}: {}",
                        paper.versioned_id, paper.title
                    ));
                    match self.catalog.download(paper, &self.repository.path(paper)) {
                        Ok(()) => {}
                        Err(error)
                            if error
                                .downcast_ref::<HttpStatusError>()
                                .is_some_and(|status| status.0 == 404) =>
                        {
                            (self.emit)(format!(
                                "Unavailable PDF (404), skipped: {}",
                                paper.versioned_id
                            ));
                            continue;
                        }
                        Err(error) => return Err(error),
                    }
                    self.repository.record(paper)?;
                    self.downloaded += 1;
                    self.count += 1;
                }
                self.repository.link(paper, folder)?;
                (self.emit)(format!(
                    "Saved {} · skipped {} · {category}",
                    self.downloaded, self.skipped
                ));
                if limit.is_some_and(|value| self.count >= value) {
                    return Ok(());
                }
            }
            offset += length;
            if offset < total {
                let (new_total, next_page) = self.catalog.page(category, start, end, offset)?;
                if new_total != total {
                    bail!("Search results changed during paging. Rerun to reconcile.");
                }
                page = next_page;
            }
        }
        Ok(())
    }
    pub fn run(&mut self, settings: &Settings) -> Result<(usize, usize)> {
        let snapshot = Utc::now()
            .with_second(0)
            .unwrap()
            .with_nanosecond(0)
            .unwrap();
        for (category, folder) in settings.mappings()? {
            let mut since = Utc.with_ymd_and_hms(1991, 1, 1, 0, 0, 0).unwrap();
            if settings.mode == "daily" {
                since = match self.repository.checkpoint(&category)? {
                    Some(value) => DateTime::parse_from_rfc3339(&value)?.with_timezone(&Utc),
                    None => snapshot,
                } - Duration::days(7);
            }
            self.count = 0;
            let limit = if settings.mode == "limited" {
                Some(settings.limit)
            } else {
                None
            };
            (self.emit)(format!("Scanning {category} → {folder}"));
            for (start, end) in windows(since, snapshot) {
                self.window(&category, &folder, start, end, limit)?;
                if limit.is_some_and(|value| self.count >= value) {
                    break;
                }
            }
            self.stop.check()?;
            if settings.mode == "daily" {
                self.repository
                    .complete(&category, &snapshot.to_rfc3339())?;
            }
        }
        (self.emit)(format!(
            "Finished. Saved {}; skipped {}.",
            self.downloaded, self.skipped
        ));
        Ok((self.downloaded, self.skipped))
    }
}
pub fn execute(settings: &Settings, stop: Cancellation, emit: impl FnMut(String)) -> Result<()> {
    let _lock = DownloadLock::acquire()?;
    let repository = Repository::new(Path::new(&settings.root))?;
    let catalog = crate::network::ArxivClient::new(
        stop.clone(),
        crate::domain::config_dir().join("cache"),
        &settings.contact,
    )?;
    DownloadService::new(catalog, repository, stop, emit).run(settings)?;
    Ok(())
}
