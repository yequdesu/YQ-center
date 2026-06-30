use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncRead, BufReader};

use crate::execution_context::ExecutionContext;

#[derive(Debug, Clone, PartialEq)]
pub struct CrocProgress {
    pub progress_pct: Option<u64>,
    pub bytes_transferred: Option<u64>,
    pub total_bytes: Option<u64>,
    pub rate_bytes_per_sec: Option<u64>,
    pub eta_sec: Option<u64>,
}

impl CrocProgress {
    pub fn to_json(&self) -> Value {
        json!({
            "progress_pct": self.progress_pct,
            "bytes_transferred": self.bytes_transferred,
            "total_bytes": self.total_bytes,
            "rate_bytes_per_sec": self.rate_bytes_per_sec,
            "eta_sec": self.eta_sec,
            "progress_source": "croc_stderr",
        })
    }
}

pub fn parse_croc_progress_line(line: &str) -> Option<CrocProgress> {
    let pct = parse_percent(line)?;
    let mut progress = CrocProgress {
        progress_pct: Some(pct),
        bytes_transferred: None,
        total_bytes: None,
        rate_bytes_per_sec: None,
        eta_sec: parse_eta(line),
    };

    if let Some((transferred, total, rate)) = parse_transfer_tuple(line) {
        progress.bytes_transferred = transferred;
        progress.total_bytes = total;
        progress.rate_bytes_per_sec = rate;
    }

    Some(progress)
}

pub async fn collect_stderr_lines_with_progress<R, F>(
    stderr: R,
    ctx: Option<Arc<ExecutionContext>>,
    base_payload: Value,
    redact: F,
) -> Vec<String>
where
    R: AsyncRead + Unpin,
    F: Fn(&str) -> String,
{
    let reader = BufReader::new(stderr);
    let mut line_stream = reader.lines();
    let mut lines = Vec::new();
    let mut last_emit: Option<tokio::time::Instant> = None;

    while let Ok(Some(line)) = line_stream.next_line().await {
        let redacted = redact(&line);
        if let (Some(ctx), Some(progress)) = (ctx.as_ref(), parse_croc_progress_line(&line)) {
            let now = tokio::time::Instant::now();
            let is_final = progress.progress_pct == Some(100);
            if is_final
                || last_emit.map_or(true, |last| {
                    now.duration_since(last) >= Duration::from_secs(1)
                })
            {
                let mut payload = base_payload.as_object().cloned().unwrap_or_default();
                if let Some(progress_object) = progress.to_json().as_object() {
                    for (key, value) in progress_object {
                        if !value.is_null() {
                            if key == "total_bytes"
                                && payload
                                    .get("total_bytes")
                                    .map_or(false, |existing| !existing.is_null())
                            {
                                continue;
                            }
                            payload.insert(key.clone(), value.clone());
                        }
                    }
                }
                payload.insert("status".into(), json!("running"));
                let progress_message = payload
                    .get("phase")
                    .and_then(|value| value.as_str())
                    .unwrap_or("transferring")
                    .to_string();
                payload.insert("progress_message".into(), json!(progress_message));
                payload.insert(
                    "last_progress_at".into(),
                    json!(chrono::Utc::now().to_rfc3339()),
                );
                ctx.report_progress("transfer_progress", Value::Object(payload))
                    .await;
                last_emit = Some(now);
            }
        }
        lines.push(redacted);
    }

    lines
}

fn parse_percent(line: &str) -> Option<u64> {
    let pct_index = line.find('%')?;
    let before = &line[..pct_index];
    let digits_reversed: String = before
        .chars()
        .rev()
        .skip_while(|ch| ch.is_whitespace())
        .take_while(|ch| ch.is_ascii_digit())
        .collect();
    if digits_reversed.is_empty() {
        return None;
    }
    let digits: String = digits_reversed.chars().rev().collect();
    digits.parse::<u64>().ok().map(|value| value.min(100))
}

fn parse_transfer_tuple(line: &str) -> Option<(Option<u64>, Option<u64>, Option<u64>)> {
    let start = line.find('(')?;
    let end = line[start..].find(')')? + start;
    let inner = &line[start + 1..end];
    let parts: Vec<&str> = inner.split(',').map(str::trim).collect();
    let (transferred, total) = parts
        .first()
        .and_then(|part| parse_byte_pair(part))
        .unwrap_or((None, None));
    let rate = parts.get(1).and_then(|part| parse_rate(part));
    Some((transferred, total, rate))
}

fn parse_byte_pair(text: &str) -> Option<(Option<u64>, Option<u64>)> {
    let (left, right) = text.split_once('/')?;
    let right_tokens: Vec<&str> = right.split_whitespace().collect();
    if right_tokens.len() < 2 {
        return None;
    }
    let unit = right_tokens[1];
    let transferred = parse_decimal_bytes(left.trim(), unit);
    let total = parse_decimal_bytes(right_tokens[0], unit);
    Some((transferred, total))
}

fn parse_rate(text: &str) -> Option<u64> {
    let cleaned = text.trim().trim_end_matches("/s");
    let tokens: Vec<&str> = cleaned.split_whitespace().collect();
    if tokens.len() < 2 {
        return None;
    }
    parse_decimal_bytes(tokens[0], tokens[1])
}

fn parse_decimal_bytes(number_text: &str, unit: &str) -> Option<u64> {
    let value = number_text.trim().parse::<f64>().ok()?;
    let multiplier = match unit {
        "B" => 1.0,
        "kB" | "KB" => 1_000.0,
        "MB" => 1_000_000.0,
        "GB" => 1_000_000_000.0,
        "KiB" => 1024.0,
        "MiB" => 1024.0 * 1024.0,
        "GiB" => 1024.0 * 1024.0 * 1024.0,
        _ => return None,
    };
    Some((value * multiplier).round() as u64)
}

fn parse_eta(line: &str) -> Option<u64> {
    let start = line.rfind('[')?;
    let end = line[start..].find(']')? + start;
    let inner = &line[start + 1..end];
    let (_, remaining) = inner.split_once(':')?;
    parse_duration_seconds(remaining.trim())
}

fn parse_duration_seconds(text: &str) -> Option<u64> {
    let mut digits = String::new();
    for ch in text.chars() {
        if ch.is_ascii_digit() {
            digits.push(ch);
        } else if !digits.is_empty() {
            break;
        }
    }
    if digits.is_empty() {
        None
    } else {
        digits.parse::<u64>().ok()
    }
}

#[cfg(test)]
mod tests {
    use super::parse_croc_progress_line;

    #[test]
    fn parses_croc_progress_line() {
        let progress =
            parse_croc_progress_line("src.bin  92% |████| (7.7/8.4 MB, 524 kB/s) [13s:1s]")
                .expect("progress");

        assert_eq!(progress.progress_pct, Some(92));
        assert_eq!(progress.bytes_transferred, Some(7_700_000));
        assert_eq!(progress.total_bytes, Some(8_400_000));
        assert_eq!(progress.rate_bytes_per_sec, Some(524_000));
        assert_eq!(progress.eta_sec, Some(1));
    }

    #[test]
    fn ignores_non_progress_lines() {
        assert!(parse_croc_progress_line("securing channel...").is_none());
    }
}
