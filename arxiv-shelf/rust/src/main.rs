mod domain;
mod network;
mod service;
mod storage;
#[cfg(test)]
mod tests;

use anyhow::{bail, Context, Result};
use base64::{engine::general_purpose::STANDARD, Engine};
use domain::{config_dir, Settings};
use network::Cancellation;
use std::{
    env, fs,
    io::{BufRead, BufReader, Write},
    path::{Path, PathBuf},
    process::{ChildStdin, Command, Stdio},
    sync::mpsc,
    thread,
};

enum Event {
    Command(String),
    Log(String),
    Error(String),
    Done,
    Closed,
}
struct Desktop {
    config: PathBuf,
}
impl Desktop {
    fn send(input: &mut ChildStdin, action: &str, values: &[&str]) -> Result<()> {
        let mut fields = vec![action.to_owned()];
        fields.extend(values.iter().map(|value| STANDARD.encode(value.as_bytes())));
        writeln!(input, "{}", fields.join("\t"))?;
        input.flush()?;
        Ok(())
    }
    fn run(&self) -> Result<()> {
        let settings = Settings::load(&self.config)?;
        fs::create_dir_all(config_dir())?;
        let script = config_dir().join("shelf.tcl");
        fs::write(&script, include_str!("../../ui/shelf.tcl"))?;
        let mut child = Command::new("wish")
            .arg(script)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .spawn()
            .context("Install Tk: sudo apt install tk")?;
        let mut input = child.stdin.take().unwrap();
        let output = child.stdout.take().unwrap();
        let (tx, rx) = mpsc::channel();
        let reader_tx = tx.clone();
        thread::spawn(move || {
            for line in BufReader::new(output).lines() {
                match line {
                    Ok(value) => {
                        if reader_tx.send(Event::Command(value)).is_err() {
                            return;
                        }
                    }
                    Err(_) => break,
                }
            }
            let _ = reader_tx.send(Event::Closed);
        });
        Self::send(
            &mut input,
            "init",
            &[
                &settings.root,
                &settings.subjects,
                &settings.mode,
                &settings.limit.to_string(),
                &settings.contact,
                "Rust",
            ],
        )?;
        let mut worker: Option<thread::JoinHandle<()>> = None;
        let mut stop = Cancellation::new();
        let mut closing = false;
        let result = (|| -> Result<()> {
            for event in rx {
                match event {
                    Event::Closed => {
                        stop.cancel();
                        break;
                    }
                    Event::Done => {
                        if let Some(handle) = worker.take() {
                            let _ = handle.join();
                        }
                        Self::send(&mut input, "busy", &["0"])?;
                        if closing {
                            break;
                        }
                    }
                    Event::Log(value) => Self::send(&mut input, "log", &[&value])?,
                    Event::Error(value) => Self::send(&mut input, "error", &[&value])?,
                    Event::Command(line) => {
                        let mut fields = line.split('\t');
                        let action = fields.next().unwrap_or("");
                        if action == "stop" || action == "quit" {
                            stop.cancel();
                            closing = action == "quit";
                            if closing && worker.is_none() {
                                break;
                            }
                            Self::send(
                                &mut input,
                                "log",
                                &["Stopping after the current network operation…"],
                            )?;
                            continue;
                        }
                        if worker.is_some() {
                            continue;
                        }
                        let command = (|| -> Result<()> {
                            let values = fields
                                .map(|field| Ok(String::from_utf8(STANDARD.decode(field)?)?))
                                .collect::<Result<Vec<_>>>()?;
                            if values.len() != 5 {
                                bail!("Invalid UI message.");
                            }
                            let settings = Settings {
                                root: values[0].clone(),
                                subjects: values[1].clone(),
                                mode: values[2].clone(),
                                limit: values[3].parse()?,
                                contact: values[4].clone(),
                            };
                            settings.mappings()?;
                            match action {
                                "open" => {
                                    fs::create_dir_all(&settings.root)?;
                                    Command::new("xdg-open")
                                        .arg(&settings.root)
                                        .stdout(Stdio::null())
                                        .stderr(Stdio::null())
                                        .spawn()?;
                                }
                                "save" | "start" => {
                                    settings.save(&self.config)?;
                                    Self::send(&mut input, "log", &["Settings saved."])?;
                                    if action == "start" {
                                        stop = Cancellation::new();
                                        let task_stop = stop.clone();
                                        let task_tx = tx.clone();
                                        Self::send(&mut input, "busy", &["1"])?;
                                        worker = Some(thread::spawn(move || {
                                            let result = service::execute(
                                                &settings,
                                                task_stop.clone(),
                                                |text| {
                                                    let _ = task_tx.send(Event::Log(text));
                                                },
                                            );
                                            if let Err(error) = result {
                                                let event = if task_stop.check().is_err() {
                                                    Event::Log(error.to_string())
                                                } else {
                                                    Event::Error(format!("{error:#}"))
                                                };
                                                let _ = task_tx.send(event);
                                            }
                                            let _ = task_tx.send(Event::Done);
                                        }));
                                    }
                                }
                                _ => bail!("Unknown UI command."),
                            }
                            Ok(())
                        })();
                        if let Err(error) = command {
                            Self::send(&mut input, "error", &[&format!("{error:#}")])?;
                        }
                    }
                }
            }
            Ok(())
        })();
        stop.cancel();
        if let Some(handle) = worker {
            let _ = handle.join();
        }
        let _ = child.kill();
        let _ = child.wait();
        result
    }
}
fn run() -> Result<()> {
    let mut args = env::args().skip(1);
    let mut headless = false;
    let mut mode = None;
    let mut limit = None;
    let mut config = config_dir().join("settings.json");
    while let Some(argument) = args.next() {
        match argument.as_str() {
            "--headless" => headless = true,
            "--mode" => {
                mode = Some(
                    args.next()
                        .context("--mode requires limited, daily or all")?,
                )
            }
            "--limit" => {
                limit = Some(
                    args.next()
                        .context("--limit requires a number")?
                        .parse::<usize>()?,
                )
            }
            "--config" => {
                config = Path::new(&args.next().context("--config requires a path")?).to_path_buf()
            }
            "--help" | "-h" => {
                println!("arxiv-shelf [--headless] [--mode limited|daily|all] [--limit N] [--config PATH]");
                return Ok(());
            }
            _ => bail!("Unknown argument: {argument}"),
        }
    }
    if !headless {
        return Desktop { config }.run();
    }
    let mut settings = Settings::load(&config)?;
    if let Some(value) = mode {
        settings.mode = value;
    }
    if let Some(value) = limit {
        settings.limit = value;
    }
    settings.mappings()?;
    let stop = Cancellation::new();
    let signal_stop = stop.clone();
    ctrlc::set_handler(move || signal_stop.cancel())?;
    service::execute(&settings, stop, |text| println!("{text}"))
}
fn main() {
    if let Err(error) = run() {
        eprintln!("{error:#}");
        std::process::exit(1);
    }
}
