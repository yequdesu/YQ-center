use rusqlite::{params, Connection};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::path::Path;
use std::sync::Mutex;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JobRecord {
    pub job_id: String,
    pub function_name: String,
    pub status: String, // claimed | running | succeeded | failed
    pub input: Option<Value>,
    pub output: Option<Value>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
    pub error_details: Option<Value>,
    pub created_at: String,
    pub updated_at: String,
    pub confirmed: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuditEntry {
    pub timestamp: String,
    pub direction: String,
    pub message_type: String,
    pub message_id: String,
    pub trace_id: Option<String>,
    pub http_status: Option<i32>,
    pub duration_ms: Option<i64>,
    pub error_kind: Option<String>,
    pub error_message: Option<String>,
    pub retry_attempt: u32,
}

pub struct JobStore {
    conn: Mutex<Connection>,
}

impl JobStore {
    pub fn open(path: &Path) -> Result<Self, rusqlite::Error> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        let conn = Connection::open(path)?;
        conn.execute_batch(
            "
            CREATE TABLE IF NOT EXISTS job_records (
                id INTEGER PRIMARY KEY,
                job_id TEXT NOT NULL UNIQUE,
                function_name TEXT NOT NULL,
                status TEXT NOT NULL,
                input TEXT,
                output TEXT,
                error_code TEXT,
                error_message TEXT,
                error_details TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                direction TEXT NOT NULL,
                message_type TEXT NOT NULL,
                message_id TEXT NOT NULL,
                trace_id TEXT,
                http_status INTEGER,
                duration_ms INTEGER,
                error_kind TEXT,
                error_message TEXT,
                retry_attempt INTEGER NOT NULL DEFAULT 0
            );
            PRAGMA journal_mode = WAL;
            PRAGMA foreign_keys = ON;
        ",
        )?;
        Ok(Self {
            conn: Mutex::new(conn),
        })
    }

    pub fn insert_job(&self, record: &JobRecord) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO job_records (job_id, function_name, status, input, output,
             error_code, error_message, error_details, created_at, updated_at, confirmed)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11)",
            params![
                record.job_id,
                record.function_name,
                record.status,
                record.input.as_ref().map(|v| v.to_string()),
                record.output.as_ref().map(|v| v.to_string()),
                record.error_code,
                record.error_message,
                record.error_details.as_ref().map(|v| v.to_string()),
                record.created_at,
                record.updated_at,
                record.confirmed as i32,
            ],
        )?;
        Ok(())
    }

    pub fn update_job_status(
        &self,
        job_id: &str,
        status: &str,
        output: Option<&Value>,
        error: Option<(&str, &str, Option<&Value>)>,
        confirmed: bool,
    ) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let now = chrono::Utc::now().to_rfc3339();
        conn.execute(
            "UPDATE job_records SET status=?1, output=?2, error_code=?3, error_message=?4,
             error_details=?5, confirmed=?6, updated_at=?7 WHERE job_id=?8",
            params![
                status,
                output.map(|v| v.to_string()),
                error.map(|(c, _, _)| c),
                error.map(|(_, m, _)| m),
                error.and_then(|(_, _, d)| d).map(|v| v.to_string()),
                confirmed as i32,
                now,
                job_id,
            ],
        )?;
        Ok(())
    }

    pub fn get_unconfirmed_jobs(&self) -> Result<Vec<JobRecord>, rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        let mut stmt = conn.prepare(
            "SELECT job_id, function_name, status, input, output, error_code, error_message,
             error_details, created_at, updated_at, confirmed
             FROM job_records WHERE confirmed = 0",
        )?;
        let records = stmt.query_map([], |row| {
            let input_str: Option<String> = row.get(3)?;
            let output_str: Option<String> = row.get(4)?;
            let err_details_str: Option<String> = row.get(7)?;
            Ok(JobRecord {
                job_id: row.get(0)?,
                function_name: row.get(1)?,
                status: row.get(2)?,
                input: input_str.and_then(|s| serde_json::from_str(&s).ok()),
                output: output_str.and_then(|s| serde_json::from_str(&s).ok()),
                error_code: row.get(5)?,
                error_message: row.get(6)?,
                error_details: err_details_str.and_then(|s| serde_json::from_str(&s).ok()),
                created_at: row.get(8)?,
                updated_at: row.get(9)?,
                confirmed: row.get::<_, i32>(10)? != 0,
            })
        })?;
        records.collect()
    }

    pub fn record_audit(&self, entry: &AuditEntry) -> Result<(), rusqlite::Error> {
        let conn = self.conn.lock().unwrap();
        conn.execute(
            "INSERT INTO audit_log (timestamp, direction, message_type, message_id, trace_id,
             http_status, duration_ms, error_kind, error_message, retry_attempt)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)",
            params![
                entry.timestamp,
                entry.direction,
                entry.message_type,
                entry.message_id,
                entry.trace_id,
                entry.http_status,
                entry.duration_ms,
                entry.error_kind,
                entry.error_message,
                entry.retry_attempt as i32,
            ],
        )?;
        Ok(())
    }
}
