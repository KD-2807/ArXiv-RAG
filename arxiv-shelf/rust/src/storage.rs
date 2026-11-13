use crate::domain::Paper;
use anyhow::{bail, Result};
use rusqlite::{params, Connection, OptionalExtension};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    path::{Path, PathBuf},
};

pub fn valid_pdf(path: &Path) -> bool {
    let check = || -> Result<bool> {
        let mut file = File::open(path)?;
        let mut prefix = [0u8; 5];
        file.read_exact(&mut prefix)?;
        if &prefix != b"%PDF-" {
            return Ok(false);
        }
        let length = file.metadata()?.len();
        file.seek(SeekFrom::Start(length.saturating_sub(2048)))?;
        let mut tail = Vec::new();
        file.read_to_end(&mut tail)?;
        Ok(tail.windows(5).any(|value| value == b"%%EOF"))
    };
    check().unwrap_or(false)
}
pub fn digest(path: &Path) -> Result<String> {
    let mut file = File::open(path)?;
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 65536];
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        hash.update(&buffer[..count]);
    }
    Ok(format!("{:x}", hash.finalize()))
}
pub trait PaperStore {
    fn path(&self, paper: &Paper) -> PathBuf;
    fn contains(&self, paper: &Paper) -> Result<bool>;
    fn record(&self, paper: &Paper) -> Result<()>;
    fn link(&self, paper: &Paper, folder: &str) -> Result<()>;
    fn checkpoint(&self, category: &str) -> Result<Option<String>>;
    fn complete(&self, category: &str, timestamp: &str) -> Result<()>;
}
pub struct Repository {
    pub root: PathBuf,
    objects: PathBuf,
    db: Connection,
}
impl Repository {
    pub fn new(root: &Path) -> Result<Self> {
        fs::create_dir_all(root)?;
        let root = root.canonicalize()?;
        let marker = root.join(".variant");
        if marker.exists() && fs::read_to_string(&marker)? != "rust" {
            bail!("This directory belongs to Python. Choose a separate directory.");
        }
        fs::write(marker, "rust")?;
        let objects = root.join(".objects");
        fs::create_dir_all(&objects)?;
        if objects.is_symlink() {
            bail!("The .objects directory must not be a symbolic link.");
        }
        let db = Connection::open(root.join("catalog.sqlite3"))?;
        db.execute_batch("CREATE TABLE IF NOT EXISTS papers(id TEXT PRIMARY KEY, metadata TEXT NOT NULL, sha256 TEXT NOT NULL); CREATE TABLE IF NOT EXISTS checkpoints(category TEXT PRIMARY KEY, timestamp TEXT NOT NULL);")?;
        Ok(Self { root, objects, db })
    }
}
impl PaperStore for Repository {
    fn path(&self, paper: &Paper) -> PathBuf {
        self.objects.join(paper.filename())
    }
    fn contains(&self, paper: &Paper) -> Result<bool> {
        let path = self.path(paper);
        if !valid_pdf(&path) {
            return Ok(false);
        }
        let stored: Option<String> = self
            .db
            .query_row(
                "SELECT sha256 FROM papers WHERE id=?",
                [&paper.identifier],
                |row| row.get(0),
            )
            .optional()?;
        match stored {
            Some(hash) => Ok(digest(&path)? == hash),
            None => {
                self.record(paper)?;
                Ok(true)
            }
        }
    }
    fn record(&self, paper: &Paper) -> Result<()> {
        let path = self.path(paper);
        if !valid_pdf(&path) {
            bail!("Downloaded file is not a complete PDF.");
        }
        self.db.execute(
            "INSERT OR REPLACE INTO papers VALUES(?,?,?)",
            params![
                paper.identifier,
                serde_json::to_string(paper)?,
                digest(&path)?
            ],
        )?;
        Ok(())
    }
    fn link(&self, paper: &Paper, folder: &str) -> Result<()> {
        use std::os::unix::fs::MetadataExt;
        let directory = self.root.join(folder);
        fs::create_dir_all(&directory)?;
        if directory.is_symlink() || directory.canonicalize()?.parent() != Some(&self.root) {
            bail!("Subject folder must be inside the download directory.");
        }
        let source = self.path(paper);
        let target = directory.join(paper.filename());
        if !target.is_symlink() {
            if let (Ok(a), Ok(b)) = (source.metadata(), target.metadata()) {
                if a.ino() == b.ino() && a.dev() == b.dev() {
                    return Ok(());
                }
            }
        }
        let temp = target.with_extension("link.tmp");
        if temp.exists() || temp.is_symlink() {
            fs::remove_file(&temp)?;
        }
        if fs::hard_link(&source, &temp).is_err() {
            fs::copy(&source, &temp)?;
        }
        fs::rename(temp, target)?;
        Ok(())
    }
    fn checkpoint(&self, category: &str) -> Result<Option<String>> {
        Ok(self
            .db
            .query_row(
                "SELECT timestamp FROM checkpoints WHERE category=?",
                [category],
                |row| row.get(0),
            )
            .optional()?)
    }
    fn complete(&self, category: &str, timestamp: &str) -> Result<()> {
        self.db.execute(
            "INSERT OR REPLACE INTO checkpoints VALUES(?,?)",
            [category, timestamp],
        )?;
        Ok(())
    }
}
