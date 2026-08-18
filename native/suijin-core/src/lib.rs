//! suijin-core — the compiled heart of the Suijin kernel.
//!
//! Exactly TWO functions cross the Python boundary, both pure
//! data-in/data-out (JSON string -> JSON string): no Python objects, no
//! refcounting, no GIL games. The Python kernel ships byte-identical
//! pure implementations as test oracles; CI asserts both agree.
//!
//!   resolve_dag(manifests_json) -> boot report JSON
//!     The scene analysis: topological boot order, cycle NAMING,
//!     missing-dependency skips, tier collision policy, broken-manifest
//!     quarantine, core-abort detection.
//!
//!   check_paths(paths_json) -> verdicts JSON
//!     VFS boundary checks: is each resolved path inside the workspace
//!     root (or an allowlist entry)?

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet};

// ─── input/output types ───────────────────────────────────────────────

#[derive(Debug, Clone, Deserialize)]
struct ManifestIn {
    id: String,
    #[serde(default)]
    #[allow(dead_code)] // schema fidelity with the Python oracle input
    version: String,
    #[serde(default = "default_tier")]
    tier: String,
    #[serde(default)]
    requires: Vec<String>,
    #[serde(default)]
    overrides: Vec<String>,
    #[serde(default)]
    broken: Option<String>,
}

fn default_tier() -> String {
    "recommended".to_string()
}

fn tier_value(tier: &str) -> u8 {
    match tier {
        "core" => 0,
        "recommended" => 1,
        _ => 2,
    }
}

#[derive(Debug, Serialize)]
struct DagReport {
    boot_order: Vec<String>,
    bootable: Vec<String>,
    skipped: BTreeMap<String, String>,
    quarantined: BTreeMap<String, String>,
    collisions: Vec<(String, String)>,
    overridden: Vec<String>,
    aborted: bool,
    abort_reason: String,
}

#[derive(Debug, Deserialize)]
struct PathsIn {
    root: String,
    #[serde(default)]
    allow: Vec<String>,
    paths: Vec<String>,
}

// ─── resolve_dag ──────────────────────────────────────────────────────

fn resolve_dag_impl(manifests: &[ManifestIn]) -> DagReport {
    let mut report = DagReport {
        boot_order: Vec::new(),
        bootable: Vec::new(),
        skipped: BTreeMap::new(),
        quarantined: BTreeMap::new(),
        collisions: Vec::new(),
        overridden: Vec::new(),
        aborted: false,
        abort_reason: String::new(),
    };

    // 1. quarantine broken manifests
    let mut by_id: HashMap<String, Vec<&ManifestIn>> = HashMap::new();
    for m in manifests {
        if let Some(reason) = &m.broken {
            report.quarantined.insert(m.id.clone(), reason.clone());
        } else {
            by_id.entry(m.id.clone()).or_default().push(m);
        }
    }

    // 2. collision policy per id: lowest tier wins; a higher-tier unit
    //    declaring the id in overrides wins instead (and is recorded)
    let mut winners: HashMap<String, &ManifestIn> = HashMap::new();
    let mut ids: Vec<String> = by_id.keys().cloned().collect();
    ids.sort();
    for id in &ids {
        let mut candidates = by_id.remove(id).unwrap_or_default();
        candidates.sort_by_key(|m| tier_value(&m.tier));
        let mut winner = candidates[0];
        for cand in &candidates[1..] {
            if cand.overrides.iter().any(|o| o == id) {
                winner = cand;
                report.overridden.push(id.clone());
            } else {
                report
                    .collisions
                    .push((id.clone(), cand.tier.clone()));
            }
        }
        winners.insert(id.clone(), winner);
    }

    // 3. cycle detection (DFS coloring, cycle members recorded by name)
    let mut color: HashMap<&str, u8> = winners
        .iter()
        .map(|(k, _)| (k.as_str(), 0u8))
        .collect();
    let mut in_cycle: BTreeSet<String> = BTreeSet::new();
    let mut cycle_desc: BTreeMap<String, String> = BTreeMap::new();

    // iterative DFS to avoid recursion limits on long chains
    for start in &ids {
        if *color.get(start.as_str()).unwrap_or(&0) != 0 {
            continue;
        }
        let mut stack: Vec<(&str, usize)> = Vec::new();
        let mut path: Vec<String> = Vec::new();
        let mut discovered_cycle: Vec<String> = Vec::new();
        stack.push((start.as_str(), 0));
        path.push(start.clone());
        while let Some((node, idx)) = stack.pop() {
            if idx == 0 {
                color.insert(node, 1);
            }
            let deps: &Vec<String> = &winners[node].requires;
            if idx < deps.len() {
                stack.push((node, idx + 1));
                let dep = &deps[idx];
                if !winners.contains_key(dep.as_str()) {
                    continue; // missing handled later
                }
                let dcolor = *color.get(dep.as_str()).unwrap_or(&0);
                if dcolor == 1 {
                    // cycle: from dep..node in current path
                    let pos = path.iter().position(|p| p == dep).unwrap_or(0);
                    discovered_cycle = path[pos..].to_vec();
                    discovered_cycle.push(dep.clone());
                    break;
                } else if dcolor == 0 {
                    path.push(dep.clone());
                    stack.push((dep.as_str(), 0));
                }
            } else {
                color.insert(node, 2);
                path.pop();
            }
        }
        if !discovered_cycle.is_empty() {
            let desc = discovered_cycle.join(" -> ");
            for member in &discovered_cycle {
                in_cycle.insert(member.clone());
                cycle_desc.insert(member.clone(), format!("dependency cycle: {}", desc));
            }
        }
    }

    // 4. availability fixpoint
    let mut bootable: BTreeSet<String> = BTreeSet::new();
    let mut pending: HashMap<String, &ManifestIn> = winners.clone();
    let mut skipped: BTreeMap<String, String> = BTreeMap::new();

    for (id, desc) in &cycle_desc {
        skipped.insert(id.clone(), desc.clone());
        pending.remove(id);
    }

    let mut changed = true;
    while changed {
        changed = false;
        let mut pending_ids: Vec<String> = pending.keys().cloned().collect();
        pending_ids.sort();
        for pid in pending_ids {
            let unit = pending[&pid];
            let missing: Vec<&String> = unit
                .requires
                .iter()
                .filter(|d| !winners.contains_key(d.as_str()))
                .collect();
            if !missing.is_empty() {
                let names: Vec<String> =
                    missing.iter().map(|m| m.to_string()).collect();
                skipped.insert(pid.clone(), format!("missing dependency: {}", names.join(", ")));
                pending.remove(&pid);
                changed = true;
                continue;
            }
            let unready: Vec<&String> = unit
                .requires
                .iter()
                .filter(|d| !bootable.contains(d.as_str()))
                .collect();
            if !unready.is_empty() {
                continue;
            }
            bootable.insert(pid.clone());
            pending.remove(&pid);
            changed = true;
        }
    }
    for (pid, unit) in &pending {
        let blocked: Vec<&String> = unit
            .requires
            .iter()
            .filter(|d| !bootable.contains(d.as_str()))
            .collect();
        let names: Vec<String> = blocked.iter().map(|b| b.to_string()).collect();
        skipped.insert(pid.clone(), format!("dependencies unavailable: {}", names.join(", ")));
    }

    // 5. core-missing aborts
    let mut core_problems: Vec<String> = winners
        .iter()
        .filter(|(id, u)| tier_value(&u.tier) == 0 && skipped.contains_key(*id))
        .map(|(id, _)| id.clone())
        .collect();
    core_problems.sort(); // deterministic abort text (oracle: canonical equality)
    if !core_problems.is_empty() {
        report.aborted = true;
        let details: Vec<String> = core_problems
            .iter()
            .map(|id| format!("{} ({})", id, skipped[id]))
            .collect();
        report.abort_reason =
            format!("core module(s) unavailable: {}", details.join("; "));
        report.skipped = skipped;
        return report;
    }

    // 6. topological order (stable: alphabetical among ready)
    let mut order: Vec<String> = Vec::new();
    let mut placed: HashSet<String> = HashSet::new();
    while placed.len() < bootable.len() {
        let mut ready: Vec<String> = bootable
            .iter()
            .filter(|id| !placed.contains(*id))
            .filter(|id| {
                winners[*id]
                    .requires
                    .iter()
                    .all(|d| placed.contains(d) || !bootable.contains(d))
            })
            .cloned()
            .collect();
        if ready.is_empty() {
            break; // defensive
        }
        ready.sort();
        for r in ready {
            order.push(r.clone());
            placed.insert(r);
        }
    }

    report.boot_order = order;
    let mut b: Vec<String> = bootable.into_iter().collect();
    b.sort();
    report.bootable = b;
    report.skipped = skipped;
    report
}

// ─── check_paths ──────────────────────────────────────────────────────

fn normalize(p: &str) -> String {
    // lexically normalize: resolve . and .. without touching the filesystem
    let absolute = p.starts_with('/');
    let mut out: Vec<String> = Vec::new();
    for part in p.split('/') {
        match part {
            "" | "." => continue,
            ".." => {
                if !out.is_empty() && out.last() != Some(&"..".to_string()) {
                    out.pop();
                } else if !absolute {
                    out.push("..".to_string());
                }
            }
            other => out.push(other.to_string()),
        }
    }
    let joined = out.join("/");
    if absolute {
        format!("/{}", joined)
    } else {
        joined
    }
}

fn is_within(child: &str, base: &str) -> bool {
    if child == base {
        return true;
    }
    let child = child.trim_end_matches('/');
    let base = base.trim_end_matches('/');
    child.starts_with(&format!("{}/", base))
}

fn check_paths_impl(input: &PathsIn) -> BTreeMap<String, bool> {
    let root = normalize(&input.root);
    let allow: Vec<String> = input.allow.iter().map(|a| normalize(a)).collect();
    let mut out = BTreeMap::new();
    for raw in &input.paths {
        let joined = if raw.starts_with('/') {
            normalize(raw)
        } else {
            normalize(&format!("{}/{}", root, raw))
        };
        let allowed = is_within(&joined, &root)
            || allow.iter().any(|a| is_within(&joined, a));
        out.insert(raw.clone(), allowed);
    }
    out
}

// ─── python bindings ──────────────────────────────────────────────────

#[pyfunction]
fn resolve_dag(manifests_json: String) -> PyResult<String> {
    let manifests: Vec<ManifestIn> = serde_json::from_str(&manifests_json)
        .map_err(|e| PyValueError::new_err(format!("invalid manifests json: {}", e)))?;
    let report = resolve_dag_impl(&manifests);
    serde_json::to_string(&report)
        .map_err(|e| PyValueError::new_err(format!("serialize: {}", e)))
}

#[pyfunction]
fn check_paths(paths_json: String) -> PyResult<String> {
    let input: PathsIn = serde_json::from_str(&paths_json)
        .map_err(|e| PyValueError::new_err(format!("invalid paths json: {}", e)))?;
    let verdicts = check_paths_impl(&input);
    serde_json::to_string(&verdicts)
        .map_err(|e| PyValueError::new_err(format!("serialize: {}", e)))
}

#[pymodule]
fn suijin_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(resolve_dag, m)?)?;
    m.add_function(wrap_pyfunction!(check_paths, m)?)?;
    Ok(())
}

// ─── rust unit tests (mirrored by the Python oracle suite) ────────────

#[cfg(test)]
mod tests {
    use super::*;

    fn m(id: &str, tier: &str, requires: &[&str]) -> ManifestIn {
        ManifestIn {
            id: id.to_string(),
            version: "1.0.0".to_string(),
            tier: tier.to_string(),
            requires: requires.iter().map(|s| s.to_string()).collect(),
            overrides: vec![],
            broken: None,
        }
    }

    #[test]
    fn healthy_order() {
        let ms = vec![m("agent", "core", &["tools"]), m("tools", "core", &["platform"]), m("platform", "core", &[])];
        let r = resolve_dag_impl(&ms);
        assert_eq!(r.boot_order, vec!["platform", "tools", "agent"]);
        assert!(!r.aborted);
    }

    #[test]
    fn missing_dep_skips() {
        let ms = vec![m("a", "recommended", &["ghost"])];
        let r = resolve_dag_impl(&ms);
        assert!(r.skipped["a"].starts_with("missing dependency: ghost"));
        assert!(!r.aborted);
    }

    #[test]
    fn core_missing_aborts() {
        let ms = vec![m("tools", "core", &["ghost"])];
        let r = resolve_dag_impl(&ms);
        assert!(r.aborted);
        assert!(r.abort_reason.contains("tools"));
    }

    #[test]
    fn cycle_named() {
        let ms = vec![m("a", "recommended", &["b"]), m("b", "recommended", &["a"])];
        let r = resolve_dag_impl(&ms);
        assert!(r.skipped["a"].contains("cycle"));
        assert!(!r.aborted);
    }

    #[test]
    fn paths_basic() {
        let input = PathsIn {
            root: "/tmp/ws".to_string(),
            allow: vec!["/tmp/extra".to_string()],
            paths: vec![
                "a/b.txt".to_string(),
                "../../etc/passwd".to_string(),
                "/etc/passwd".to_string(),
                "/tmp/extra/f".to_string(),
                "/tmp/other".to_string(),
            ],
        };
        let v = check_paths_impl(&input);
        assert!(v["a/b.txt"]);
        assert!(!v["../../etc/passwd"]);
        assert!(!v["/etc/passwd"]);
        assert!(v["/tmp/extra/f"]);
        assert!(!v["/tmp/other"]);
    }
}
