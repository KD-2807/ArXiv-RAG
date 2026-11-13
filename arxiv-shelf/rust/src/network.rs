use crate::{
    domain::{Catalog, Paper},
    storage::valid_pdf,
};
use anyhow::{bail, Context, Result};
use chrono::{DateTime, Utc};
use reqwest::{blocking::Client, redirect::Policy, Url};
use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    thread,
    time::{Duration, Instant, SystemTime},
};

#[derive(Clone)]
pub struct Cancellation(pub Arc<AtomicBool>);
impl Cancellation {
    pub fn new() -> Self {
        Self(Arc::new(AtomicBool::new(false)))
    }
    pub fn cancel(&self) {
        self.0.store(true, Ordering::Relaxed);
    }
    pub fn check(&self) -> Result<()> {
        if self.0.load(Ordering::Relaxed) {
            bail!("Stopped. Completed PDFs are kept.");
        }
        Ok(())
    }
    pub fn wait(&self, duration: Duration) -> Result<()> {
        let until = Instant::now() + duration;
        while Instant::now() < until {
            self.check()?;
            thread::sleep(
                Duration::from_millis(100).min(until.saturating_duration_since(Instant::now())),
            );
        }
        self.check()
    }
}
#[derive(Debug)]
pub struct HttpStatusError(pub u16);

impl std::fmt::Display for HttpStatusError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "arXiv HTTP {}", self.0)
    }
}
impl std::error::Error for HttpStatusError {}
#[derive(Deserialize)]
struct Feed {
    #[serde(rename = "totalResults")]
    total: Option<usize>,
    #[serde(rename = "entry", default)]
    entries: Vec<Entry>,
}
#[derive(Deserialize)]
struct Entry {
    id: String,
    #[serde(default)]
    title: String,
    #[serde(default)]
    published: String,
    #[serde(default)]
    summary: String,
    #[serde(rename = "category", default)]
    categories: Vec<Category>,
}
#[derive(Deserialize)]
struct Category {
    #[serde(rename = "@term")]
    term: String,
}
pub fn parse_feed(data: &[u8]) -> Result<(usize, Vec<Paper>)> {
    let feed: Feed = quick_xml::de::from_reader(data)?;
    let mut papers = Vec::new();
    for entry in feed.entries {
        let (_, id) = entry
            .id
            .split_once("/abs/")
            .with_context(|| format!("arXiv API: {}", entry.summary))?;
        papers.push(Paper::new(
            id,
            &entry.title,
            &entry.published,
            entry.categories.into_iter().map(|c| c.term).collect(),
        )?);
    }
    Ok((
        feed.total.context("arXiv response has no result count.")?,
        papers,
    ))
}
pub struct ArxivClient {
    client: Client,
    stop: Cancellation,
    cache: PathBuf,
    next: Instant,
}
impl ArxivClient {
    pub fn new(stop: Cancellation, cache: PathBuf, contact: &str) -> Result<Self> {
        fs::create_dir_all(&cache)?;
        for entry in fs::read_dir(&cache)? {
            let path = entry?.path();
            if path.extension().is_some_and(|ext| ext == "xml")
                && path.metadata()?.modified()?.elapsed().unwrap_or_default()
                    > Duration::from_secs(86400)
            {
                fs::remove_file(path)?;
            }
        }
        let agent = format!(
            "ArxivShelf/1.0 (personal research; {})",
            if contact.is_empty() {
                "local desktop"
            } else {
                contact
            }
        );
        let client = Client::builder()
            .user_agent(agent)
            .timeout(Duration::from_secs(30))
            .redirect(Policy::custom(|attempt| {
                let url = attempt.url();
                if attempt.previous().len() >= 5
                    || url.scheme() != "https"
                    || !matches!(
                        url.host_str(),
                        Some("arxiv.org" | "export.arxiv.org" | "www.arxiv.org")
                    )
                {
                    attempt.error("Unexpected download redirect")
                } else {
                    attempt.follow()
                }
            }))
            .build()?;
        Ok(Self {
            client,
            stop,
            cache,
            next: Instant::now() + Duration::from_millis(3200),
        })
    }
    fn transfer(&mut self, url: &str, target: Option<&Path>) -> Result<Vec<u8>> {
        for attempt in 0..5 {
            self.stop
                .wait(self.next.saturating_duration_since(Instant::now()))?;
            let response = self
                .client
                .get(url)
                .header("Accept-Encoding", "identity")
                .send();
            let mut response = match response {
                Ok(value) => value,
                Err(error) => {
                    self.next = Instant::now() + Duration::from_millis(3200);
                    if attempt == 4 {
                        return Err(error.into());
                    }
                    self.stop.wait(Duration::from_secs(5 << attempt))?;
                    continue;
                }
            };
            let status = response.status();
            if !status.is_success() {
                let mut delay = Duration::from_secs(5 << attempt);
                if let Some(value) = response
                    .headers()
                    .get("Retry-After")
                    .and_then(|v| v.to_str().ok())
                {
                    let requested =
                        value
                            .parse::<u64>()
                            .map(Duration::from_secs)
                            .ok()
                            .or_else(|| {
                                httpdate::parse_http_date(value).ok().map(|date| {
                                    date.duration_since(SystemTime::now()).unwrap_or_default()
                                })
                            });
                    if let Some(requested) = requested {
                        delay = delay.max(requested);
                    }
                }
                drop(response);
                self.next = Instant::now() + Duration::from_millis(3200);
                if ![429, 500, 502, 503, 504].contains(&status.as_u16()) || attempt == 4 {
                    return Err(HttpStatusError(status.as_u16()).into());
                }
                self.stop.wait(delay)?;
                continue;
            }
            let expected = response.content_length();
            let maximum: u64 = if target.is_some() {
                512 * 1024 * 1024
            } else {
                16 * 1024 * 1024
            };
            let read = || -> Result<Vec<u8>> {
                let mut output = target.map(File::create).transpose()?;
                let mut data = Vec::new();
                let mut buffer = [0u8; 65536];
                let mut total = 0u64;
                loop {
                    self.stop.check()?;
                    let count = response.read(&mut buffer)?;
                    if count == 0 {
                        break;
                    }
                    total += count as u64;
                    if total > maximum {
                        bail!("Response exceeds the download size limit.");
                    }
                    if let Some(file) = &mut output {
                        file.write_all(&buffer[..count])?;
                    } else {
                        data.extend_from_slice(&buffer[..count]);
                    }
                }
                if expected.is_some_and(|length| length != total) {
                    bail!("Incomplete HTTP response.");
                }
                if let Some(file) = output {
                    file.sync_all()?;
                }
                Ok(data)
            }();
            drop(response);
            self.next = Instant::now() + Duration::from_millis(3200);
            match read {
                Ok(data) => return Ok(data),
                Err(error) => {
                    self.stop.check()?;
                    if attempt == 4 {
                        return Err(error);
                    }
                    self.stop.wait(Duration::from_secs(5 << attempt))?;
                }
            }
        }
        bail!("Request failed.")
    }
}
impl Catalog for ArxivClient {
    fn page(
        &mut self,
        category: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        offset: usize,
    ) -> Result<(usize, Vec<Paper>)> {
        let query = format!(
            "cat:{category} AND submittedDate:[{} TO {}]",
            start.format("%Y%m%d%H%M"),
            end.format("%Y%m%d%H%M")
        );
        let url = Url::parse_with_params(
            "https://export.arxiv.org/api/query",
            &[
                ("search_query", query),
                ("start", offset.to_string()),
                ("max_results", "100".into()),
                ("sortBy", "submittedDate".into()),
                ("sortOrder", "descending".into()),
            ],
        )?;
        let path = self
            .cache
            .join(format!("{:x}.xml", Sha256::digest(url.as_str().as_bytes())));
        let cached = path.exists();
        let data = if cached {
            fs::read(&path)?
        } else {
            self.transfer(url.as_str(), None)?
        };
        let parsed = parse_feed(&data)?;
        if !cached {
            fs::write(path, data)?;
        }
        Ok(parsed)
    }
    fn download(&mut self, paper: &Paper, target: &Path) -> Result<()> {
        let temp = target.with_extension("part");
        let result = (|| {
            self.transfer(
                &format!("https://export.arxiv.org/pdf/{}", paper.versioned_id),
                Some(&temp),
            )?;
            if !valid_pdf(&temp) {
                bail!("{}: response was not a complete PDF.", paper.identifier);
            }
            fs::rename(&temp, target)?;
            Ok(())
        })();
        let _ = fs::remove_file(temp);
        result
    }
}

#[cfg(test)]
mod live_tests {
    use super::*;
    #[test]
    #[ignore = "Makes two live arXiv requests; run manually, never in offline CI"]
    fn live_arxiv_smoke() {
        let temp = tempfile::TempDir::new().unwrap();
        let _lock = crate::service::DownloadLock::acquire().unwrap();
        let mut client =
            ArxivClient::new(Cancellation::new(), temp.path().join("cache"), "").unwrap();
        let data = client
            .transfer(
                "https://export.arxiv.org/api/query?id_list=1706.03762&max_results=1",
                None,
            )
            .unwrap();
        let (total, papers) = parse_feed(&data).unwrap();
        assert_eq!(total, 1);
        let target = temp.path().join("paper.pdf");
        client.download(&papers[0], &target).unwrap();
        assert!(valid_pdf(&target));
        println!(
            "Live metadata and PDF verified: {}, {} bytes",
            papers[0].versioned_id,
            target.metadata().unwrap().len()
        );
    }
}
