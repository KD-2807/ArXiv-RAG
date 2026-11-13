use crate::{
    domain::{Catalog, Paper, Settings},
    network::{parse_feed, Cancellation},
    service::{windows, DownloadService},
    storage::{PaperStore, Repository},
};
use anyhow::{bail, Result};
use chrono::{DateTime, TimeZone, Utc};
use std::{fs, path::Path};
use tempfile::TempDir;
const PDF: &[u8] = b"%PDF-1.4\nfixture\n%%EOF\n";
fn paper(number: usize) -> Paper {
    Paper::new(
        &format!("2611.{number:05}v1"),
        "Test",
        "2026-11-01T00:00:00Z",
        vec!["cs.SD".into()],
    )
    .unwrap()
}
struct FakeCatalog {
    items: Vec<Paper>,
    downloads: usize,
    fail: bool,
    empty: bool,
}
impl Catalog for FakeCatalog {
    fn page(
        &mut self,
        _: &str,
        _: DateTime<Utc>,
        _: DateTime<Utc>,
        offset: usize,
    ) -> Result<(usize, Vec<Paper>)> {
        if self.empty {
            return Ok((1, vec![]));
        }
        Ok((
            self.items.len(),
            self.items.iter().skip(offset).take(1).cloned().collect(),
        ))
    }
    fn download(&mut self, _: &Paper, target: &Path) -> Result<()> {
        if self.fail {
            bail!("offline");
        }
        fs::write(target, PDF)?;
        self.downloads += 1;
        Ok(())
    }
}
fn fake(items: Vec<Paper>) -> FakeCatalog {
    FakeCatalog {
        items,
        downloads: 0,
        fail: false,
        empty: false,
    }
}
fn settings(root: &Path) -> Settings {
    Settings {
        root: root.to_string_lossy().into_owned(),
        subjects: "cs.SD=Sound".into(),
        mode: "daily".into(),
        ..Default::default()
    }
}
#[test]
fn feed_namespaces_and_old_ids() {
    let (total, entries) = parse_feed(include_bytes!("../../tests/feed.xml")).unwrap();
    assert_eq!(total, 2);
    assert_eq!(entries[0].title, "Sound & Robotics");
    assert_eq!(entries[0].categories, ["cs.SD", "cs.RO"]);
    assert_eq!(entries[1].filename(), "hep-th_9901001.pdf");
}
#[test]
fn api_error_rejected() {
    assert!(parse_feed(b"<feed xmlns=\"http://www.w3.org/2005/Atom\"><entry><id>error</id><summary>bad query</summary></entry></feed>").is_err());
}
#[test]
fn validate_input() {
    let temp = TempDir::new().unwrap();
    for subjects in [
        "cs.*=All",
        "cs.SD=../escape",
        "cs.SD=Sound\ncs.SD=Other",
        "",
    ] {
        let mut config = settings(temp.path());
        config.subjects = subjects.into();
        assert!(config.mappings().is_err());
    }
    assert!(Paper::new("../../tmp/x", "", "", vec![]).is_err());
}
#[test]
fn skip_restart_and_cross_list() {
    let temp = TempDir::new().unwrap();
    let mut config = settings(temp.path());
    config.subjects = "cs.SD=Sound\ncs.RO=Robotics".into();
    let repo = Repository::new(temp.path()).unwrap();
    let mut service = DownloadService::new(fake(vec![paper(1)]), repo, Cancellation::new(), |_| {});
    service.run(&config).unwrap();
    assert_eq!(service.catalog.downloads, 1);
    service.run(&config).unwrap();
    assert_eq!(service.catalog.downloads, 1);
    use std::os::unix::fs::MetadataExt;
    assert_eq!(
        fs::metadata(temp.path().join("Sound/2611.00001.pdf"))
            .unwrap()
            .ino(),
        fs::metadata(temp.path().join("Robotics/2611.00001.pdf"))
            .unwrap()
            .ino()
    );
}
#[test]
fn count_only_new_downloads() {
    let temp = TempDir::new().unwrap();
    let repo = Repository::new(temp.path()).unwrap();
    fs::write(repo.path(&paper(1)), PDF).unwrap();
    repo.record(&paper(1)).unwrap();
    let mut config = settings(temp.path());
    config.mode = "limited".into();
    config.limit = 1;
    let mut service = DownloadService::new(
        fake(vec![paper(1), paper(2), paper(3)]),
        repo,
        Cancellation::new(),
        |_| {},
    );
    service.run(&config).unwrap();
    assert_eq!(service.catalog.downloads, 1);
    assert!(service.repository.contains(&paper(2)).unwrap());
    assert!(!service.repository.contains(&paper(3)).unwrap());
}
#[test]
fn corruption_and_recovery() {
    let temp = TempDir::new().unwrap();
    let repo = Repository::new(temp.path()).unwrap();
    let item = paper(1);
    fs::write(repo.path(&item), PDF).unwrap();
    assert!(repo.contains(&item).unwrap());
    fs::write(repo.path(&item), b"%PDF-1.4\nchanged\n%%EOF\n").unwrap();
    assert!(!repo.contains(&item).unwrap());
    fs::remove_file(repo.path(&item)).unwrap();
    assert!(!repo.contains(&item).unwrap());
}
#[test]
fn failure_does_not_advance_checkpoint() {
    let temp = TempDir::new().unwrap();
    let repo = Repository::new(temp.path()).unwrap();
    let mut source = fake(vec![paper(1)]);
    source.fail = true;
    let mut service = DownloadService::new(source, repo, Cancellation::new(), |_| {});
    assert!(service.run(&settings(temp.path())).is_err());
    assert!(service.repository.checkpoint("cs.SD").unwrap().is_none());
}
#[test]
fn cancel_does_not_download() {
    let temp = TempDir::new().unwrap();
    let stop = Cancellation::new();
    stop.cancel();
    let mut service = DownloadService::new(
        fake(vec![paper(1)]),
        Repository::new(temp.path()).unwrap(),
        stop,
        |_| {},
    );
    assert!(service.run(&settings(temp.path())).is_err());
    assert_eq!(service.catalog.downloads, 0);
}
#[test]
fn subject_symlink_rejected() {
    let temp = TempDir::new().unwrap();
    let other = TempDir::new().unwrap();
    let repo = Repository::new(temp.path()).unwrap();
    fs::write(repo.path(&paper(1)), PDF).unwrap();
    std::os::unix::fs::symlink(other.path(), temp.path().join("Sound")).unwrap();
    assert!(repo.link(&paper(1), "Sound").is_err());
}
#[test]
fn empty_page_is_not_success() {
    let temp = TempDir::new().unwrap();
    let mut source = fake(vec![]);
    source.empty = true;
    let mut service = DownloadService::new(
        source,
        Repository::new(temp.path()).unwrap(),
        Cancellation::new(),
        |_| {},
    );
    assert!(service.run(&settings(temp.path())).is_err());
}
#[test]
fn date_windows_have_no_gaps() {
    let start = Utc.with_ymd_and_hms(2026, 10, 31, 23, 59, 0).unwrap();
    let end = Utc.with_ymd_and_hms(2026, 11, 1, 0, 1, 0).unwrap();
    let result = windows(start, end);
    assert_eq!(result.len(), 2);
    assert_eq!((result[0].0 - result[1].1).num_minutes(), 1);
}
#[test]
fn separate_storage_enforced() {
    let temp = TempDir::new().unwrap();
    fs::write(temp.path().join(".variant"), "python").unwrap();
    assert!(Repository::new(temp.path()).is_err());
}
