//! Local transfer ledger for tracking croc transfer state.
//!
//! Persists transfer facts to a local SQLite database so that:
//! - Transfers are idempotent per `transfer_id`
//! - Daemon restarts can detect interrupted/running transfers
//! - Source file changes can be detected via mtime/sha256 comparison
//! - Resume mode semantics are enforced locally

use chrono::Utc;
use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use std::path::Path;
use std::sync::Mutex;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum TransferRole {
    Sender,
    Receiver,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum TransferStatus {
    Created,
    Running,
    Interrupted,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum ResumeMode {
    Resume,
    Overwrite,
    FailIfExists,
}

impl Default for ResumeMode {
    fn default() -> Self {
        Self::Resume
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TransferLedgerEntry {
    pub transfer_id: String,
    pub role: TransferRole,
    pub status: TransferStatus,
    pub code_hash: String,
    pub relay_url: Option<String>,
    pub source_path: Option<String>,
    pub target_path: Option<String>,
    pub output_dir: Option<String>,
    pub source_size_bytes: Option<u64>,
    pub source_mtime: Option<String>,
    pub source_sha256: Option<String>,
    pub partial_path: Option<String>,
    pub resume_mode: ResumeMode,
    pub attempt_count: u32,
    pub pid: Option<u32>,
    pub started_at: Option<String>,
    pub last_progress_at: Option<String>,
    pub completed_at: Option<String>,
    pub last_error_code: Option<String>,
    pub last_error_message: Option<String>,
    pub created_at: String,
    pub updated_at: String,
}

pub struct TransferLedger {
    conn: Mutex<Connection>,
}

impl TransferLedger {
    pub fn open(path: &Path) -> Result<Self, rusqlite::Error> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let conn = Connection::open(path)?;
        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS transfer_ledger (
                id INTEGER PRIMARY KEY,
                transfer_id TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                status TEXT NOT NULL,
                code_hash TEXT NOT NULL,
                relay_url TEXT,
                source_path TEXT,
                target_path TEXT,
                output_dir TEXT,
                source_size_bytes INTEGER,
                source_mtime TEXT,
                source_sha256 TEXT,
                partial_path TEXT,
                resume_mode TEXT NOT NULL DEFAULT 'resume',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                pid INTEGER,
                started_at TEXT,
                last_progress_at TEXT,
                completed_at TEXT,
                last_error_code TEXT,
                last_error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            PRAGMA journal_mode = WAL;
        ",
        )?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    /// Insert a new transfer entry. Returns error if transfer_id already exists.
    pub fn insert(&self, entry: &TransferLedgerEntry) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO transfer_ledger (
                transfer_id, role, status, code_hash, relay_url, source_path,
                target_path, output_dir, source_size_bytes, source_mtime, source_sha256,
                partial_path, resume_mode, attempt_count, pid, started_at,
                last_progress_at, completed_at, last_error_code, last_error_message,
                created_at, updated_at
            ) VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, ?12, ?13, ?14, ?15, ?16, ?17, ?18, ?19, ?20, ?21, ?22)",
            params![
                entry.transfer_id,
                serde_json::to_string(&entry.role).unwrap_or_default().trim_matches('"'),
                serde_json::to_string(&entry.status).unwrap_or_default().trim_matches('"'),
                entry.code_hash,
                entry.relay_url,
                entry.source_path,
                entry.target_path,
                entry.output_dir,
                entry.source_size_bytes.map(|v| v as i64),
                entry.source_mtime,
                entry.source_sha256,
                entry.partial_path,
                serde_json::to_string(&entry.resume_mode).unwrap_or_default().trim_matches('"'),
                entry.attempt_count as i32,
                entry.pid.map(|v| v as i64),
                entry.started_at,
                entry.last_progress_at,
                entry.completed_at,
                entry.last_error_code,
                entry.last_error_message,
                entry.created_at,
                entry.updated_at,
            ],
        )?;
        Ok(())
    }

    /// Update transfer status and related fields.
    pub fn update_status(
        &self,
        transfer_id: &str,
        status: TransferStatus,
        pid: Option<u32>,
        error: Option<(&str, &str)>,
    ) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let now = Utc::now().to_rfc3339();
        let (error_code, error_message) = error.unzip();
        conn.execute(
            "UPDATE transfer_ledger SET status = ?1, pid = ?2, last_error_code = ?3,
             last_error_message = ?4, updated_at = ?5,
             completed_at = CASE WHEN ?1 IN ('succeeded', 'failed', 'cancelled') THEN ?5 ELSE completed_at END
             WHERE transfer_id = ?6",
            params![
                serde_json::to_string(&status).unwrap_or_default().trim_matches('"'),
                pid.map(|v| v as i64),
                error_code,
                error_message,
                now,
                transfer_id,
            ],
        )?;
        Ok(())
    }

    /// Record progress timestamp.
    pub fn record_progress(&self, transfer_id: &str) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let now = Utc::now().to_rfc3339();
        conn.execute(
            "UPDATE transfer_ledger SET last_progress_at = ?1, updated_at = ?1 WHERE transfer_id = ?2",
            params![now, transfer_id],
        )?;
        Ok(())
    }

    /// Increment attempt count.
    pub fn increment_attempt(&self, transfer_id: &str) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let now = Utc::now().to_rfc3339();
        conn.execute(
            "UPDATE transfer_ledger SET attempt_count = attempt_count + 1, updated_at = ?1 WHERE transfer_id = ?2",
            params![now, transfer_id],
        )?;
        Ok(())
    }

    /// Get a transfer entry by ID.
    pub fn get(&self, transfer_id: &str) -> Result<Option<TransferLedgerEntry>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT transfer_id, role, status, code_hash, relay_url, source_path,
             target_path, output_dir, source_size_bytes, source_mtime, source_sha256,
             partial_path, resume_mode, attempt_count, pid, started_at,
             last_progress_at, completed_at, last_error_code, last_error_message,
             created_at, updated_at
             FROM transfer_ledger WHERE transfer_id = ?1",
        )?;
        let mut rows = stmt.query_map(params![transfer_id], |row| {
            Ok(TransferLedgerEntry {
                transfer_id: row.get(0)?,
                role: serde_json::from_str(&format!("\"{}\"", row.get::<_, String>(1)?))
                    .unwrap_or(TransferRole::Sender),
                status: serde_json::from_str(&format!("\"{}\"", row.get::<_, String>(2)?))
                    .unwrap_or(TransferStatus::Created),
                code_hash: row.get(3)?,
                relay_url: row.get(4)?,
                source_path: row.get(5)?,
                target_path: row.get(6)?,
                output_dir: row.get(7)?,
                source_size_bytes: row.get::<_, Option<i64>>(8)?.map(|v| v as u64),
                source_mtime: row.get(9)?,
                source_sha256: row.get(10)?,
                partial_path: row.get(11)?,
                resume_mode: serde_json::from_str(&format!("\"{}\"", row.get::<_, String>(12)?))
                    .unwrap_or(ResumeMode::Resume),
                attempt_count: row.get::<_, i32>(13)? as u32,
                pid: row.get::<_, Option<i64>>(14)?.map(|v| v as u32),
                started_at: row.get(15)?,
                last_progress_at: row.get(16)?,
                completed_at: row.get(17)?,
                last_error_code: row.get(18)?,
                last_error_message: row.get(19)?,
                created_at: row.get(20)?,
                updated_at: row.get(21)?,
            })
        })?;
        match rows.next() {
            Some(row) => Ok(Some(row?)),
            None => Ok(None),
        }
    }

    /// List all entries, optionally filtered by status.
    pub fn list(
        &self,
        status_filter: Option<&TransferStatus>,
    ) -> Result<Vec<TransferLedgerEntry>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let (sql, params_vec): (String, Vec<Box<dyn rusqlite::types::ToSql>>) = match status_filter
        {
            Some(status) => {
                let s = serde_json::to_string(status)
                    .unwrap_or_default()
                    .trim_matches('"')
                    .to_string();
                (
                    "SELECT transfer_id, role, status, code_hash, relay_url, source_path,
                     target_path, output_dir, source_size_bytes, source_mtime, source_sha256,
                     partial_path, resume_mode, attempt_count, pid, started_at,
                     last_progress_at, completed_at, last_error_code, last_error_message,
                     created_at, updated_at
                     FROM transfer_ledger WHERE status = ?1 ORDER BY created_at DESC"
                        .into(),
                    vec![Box::new(s)],
                )
            }
            None => (
                "SELECT transfer_id, role, status, code_hash, relay_url, source_path,
                 target_path, output_dir, source_size_bytes, source_mtime, source_sha256,
                 partial_path, resume_mode, attempt_count, pid, started_at,
                 last_progress_at, completed_at, last_error_code, last_error_message,
                 created_at, updated_at
                 FROM transfer_ledger ORDER BY created_at DESC"
                    .into(),
                vec![],
            ),
        };
        let mut stmt = conn.prepare(&sql)?;
        let rows = stmt.query_map(rusqlite::params_from_iter(params_vec.iter()), |row| {
            Ok(TransferLedgerEntry {
                transfer_id: row.get(0)?,
                role: serde_json::from_str(&format!("\"{}\"", row.get::<_, String>(1)?))
                    .unwrap_or(TransferRole::Sender),
                status: serde_json::from_str(&format!("\"{}\"", row.get::<_, String>(2)?))
                    .unwrap_or(TransferStatus::Created),
                code_hash: row.get(3)?,
                relay_url: row.get(4)?,
                source_path: row.get(5)?,
                target_path: row.get(6)?,
                output_dir: row.get(7)?,
                source_size_bytes: row.get::<_, Option<i64>>(8)?.map(|v| v as u64),
                source_mtime: row.get(9)?,
                source_sha256: row.get(10)?,
                partial_path: row.get(11)?,
                resume_mode: serde_json::from_str(&format!("\"{}\"", row.get::<_, String>(12)?))
                    .unwrap_or(ResumeMode::Resume),
                attempt_count: row.get::<_, i32>(13)? as u32,
                pid: row.get::<_, Option<i64>>(14)?.map(|v| v as u32),
                started_at: row.get(15)?,
                last_progress_at: row.get(16)?,
                completed_at: row.get(17)?,
                last_error_code: row.get(18)?,
                last_error_message: row.get(19)?,
                created_at: row.get(20)?,
                updated_at: row.get(21)?,
            })
        })?;
        let mut entries = Vec::new();
        for row in rows {
            entries.push(row?);
        }
        Ok(entries)
    }
}
