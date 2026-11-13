use anyhow::{bail, Result};
use chrono::{DateTime, Utc};
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::{
    env, fs,
    path::{Path, PathBuf},
    sync::OnceLock,
};

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Paper {
    pub identifier: String,
    pub versioned_id: String,
    pub title: String,
    pub published: String,
    pub categories: Vec<String>,
}
impl Paper {
    pub fn new(id: &str, title: &str, published: &str, categories: Vec<String>) -> Result<Self> {
        static ID: OnceLock<Regex> = OnceLock::new();
        let pattern = ID.get_or_init(|| {
            Regex::new(r"^(?:\d{4}\.\d{4,5}|[A-Za-z-]+(?:\.[A-Z]{2})?/\d{7})v\d+$").unwrap()
        });
        if !pattern.is_match(id) {
            bail!("Invalid arXiv identifier: {id}");
        }
        let (base, _) = id.rsplit_once('v').unwrap();
        Ok(Self {
            identifier: base.into(),
            versioned_id: id.into(),
            title: title.split_whitespace().collect::<Vec<_>>().join(" "),
            published: published.into(),
            categories,
        })
    }
    pub fn filename(&self) -> String {
        format!("{}.pdf", self.identifier.replace('/', "_"))
    }
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Settings {
    pub root: String,
    pub subjects: String,
    pub mode: String,
    pub limit: usize,
    pub contact: String,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            root: home()
                .join("Downloads/ArxivShelf-Rust")
                .to_string_lossy()
                .into_owned(),
            subjects: "cs.SD=Sound\ncs.RO=Robotics".into(),
            mode: "limited".into(),
            limit: 100,
            contact: String::new(),
        }
    }
}
impl Settings {
    pub fn load(path: &Path) -> Result<Self> {
        if path.exists() {
            Ok(serde_json::from_slice(&fs::read(path)?)?)
        } else {
            Ok(Self::default())
        }
    }
    pub fn save(&self, path: &Path) -> Result<()> {
        self.mappings()?;
        fs::create_dir_all(path.parent().unwrap())?;
        let temp = path.with_extension("tmp");
        fs::write(&temp, serde_json::to_vec_pretty(self)?)?;
        fs::rename(temp, path)?;
        Ok(())
    }
    pub fn mappings(&self) -> Result<Vec<(String, String)>> {
        let pattern = Regex::new(r"^[a-z][a-z0-9-]*(?:\.[A-Z]{2})?$")?;
        let mut result: Vec<(String, String)> = Vec::new();
        for line in self.subjects.lines().filter(|line| !line.trim().is_empty()) {
            let (category, folder) = line
                .split_once('=')
                .ok_or_else(|| anyhow::anyhow!("Use category=Folder, one per line."))?;
            let (category, folder) = (category.trim(), folder.trim());
            if !pattern.is_match(category) {
                bail!("Use an exact category, for example cs.SD.");
            }
            if folder.is_empty()
                || folder.chars().count() > 80
                || !folder
                    .chars()
                    .all(|c| c.is_alphanumeric() || " _-".contains(c))
            {
                bail!("Folder names may contain letters, numbers, spaces, _ and - (max 80 characters).");
            }
            if result.iter().any(|(c, _)| c == category) {
                bail!("Duplicate category: {category}");
            }
            result.push((category.into(), folder.into()));
        }
        if result.is_empty() {
            bail!("Add at least one category.");
        }
        if !["limited", "daily", "all"].contains(&self.mode.as_str())
            || !(1..=1000000).contains(&self.limit)
        {
            bail!("Invalid mode or count (1–1000000).");
        }
        if !Path::new(&self.root).is_absolute() {
            bail!("Choose an absolute download directory.");
        }
        if self.contact.chars().any(|c| c.is_control()) || self.contact.chars().count() > 200 {
            bail!("Contact must be one line, at most 200 characters.");
        }
        Ok(result)
    }
}
pub fn home() -> PathBuf {
    PathBuf::from(env::var_os("HOME").unwrap_or_else(|| ".".into()))
}
pub fn config_dir() -> PathBuf {
    PathBuf::from(
        env::var_os("XDG_CONFIG_HOME").unwrap_or_else(|| home().join(".config").into_os_string()),
    )
    .join("arxiv-shelf-rust")
}
pub fn cache_dir() -> PathBuf {
    PathBuf::from(
        env::var_os("XDG_CACHE_HOME").unwrap_or_else(|| home().join(".cache").into_os_string()),
    )
    .join("arxiv-shelf")
}
pub trait Catalog {
    fn page(
        &mut self,
        category: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
        offset: usize,
    ) -> Result<(usize, Vec<Paper>)>;
    fn download(&mut self, paper: &Paper, target: &Path) -> Result<()>;
}
