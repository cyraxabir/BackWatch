"""Integrated Web UI, REST API, and Prometheus metrics server.

Provides:
- Web UI Dashboard at / and /dashboard (No Docker or Grafana needed!)
- Interactive AI Chat Assistant at /api/chat
- Prometheus metrics at /metrics
- JSON API at /api/report, /api/summary, /api/scan
- Healthcheck at /healthz
Uses standard library http.server (no heavy external framework dependencies).
"""

import base64
import hmac
import json
import logging
import os
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, Optional
from urllib.parse import parse_qs, urlparse

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

logger = logging.getLogger(__name__)

# Global server state references
_state_dir: str = "/state"
_trigger_scan_fn: Optional[Callable[[], None]] = None
_ai_config: Optional[Any] = None
_auth_config: Optional[Any] = None
_scan_lock = threading.Lock()
_is_scanning: bool = False


DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Backwatch — Backup Health Monitor & Retention</title>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --card-sub: #1f2937;
      --border: #374151;
      --border-sub: #2d3748;
      --text: #f9fafb;
      --text-muted: #9ca3af;
      --pass: #10b981;
      --pass-bg: rgba(16, 185, 129, 0.15);
      --warn: #f59e0b;
      --warn-bg: rgba(245, 158, 11, 0.15);
      --fail: #ef4444;
      --fail-bg: rgba(239, 68, 68, 0.15);
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --purple: #8b5cf6;
      --purple-bg: rgba(139, 92, 246, 0.15);
      --cyan: #06b6d4;
      --cyan-bg: rgba(6, 182, 212, 0.15);
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
      padding-bottom: 4rem;
    }
    header {
      background-color: rgba(17, 24, 39, 0.95);
      backdrop-filter: blur(10px);
      border-bottom: 1px solid var(--border);
      padding: 0.85rem 2rem;
      position: sticky;
      top: 0;
      z-index: 100;
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }
    .logo-area { display: flex; align-items: center; gap: 0.75rem; }
    .logo-icon { font-size: 1.75rem; }
    h1 { font-size: 1.25rem; font-weight: 700; color: #fff; }
    .subtitle { font-size: 0.8rem; color: var(--text-muted); }
    .header-actions { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
    .btn {
      background-color: var(--primary);
      color: white;
      border: none;
      padding: 0.45rem 0.9rem;
      border-radius: 0.375rem;
      font-weight: 600;
      font-size: 0.85rem;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s ease;
      text-decoration: none;
    }
    .btn:hover { background-color: var(--primary-hover); }
    .btn-secondary { background-color: var(--card-sub); border: 1px solid var(--border); color: var(--text); }
    .btn-secondary:hover { background-color: #374151; }
    .btn:disabled { opacity: 0.6; cursor: not-allowed; }

    .container { max-width: 1540px; margin: 1.25rem auto; padding: 0 1.5rem; display: flex; flex-direction: column; gap: 1.25rem; }

    /* System & Storage Banner */
    .storage-banner {
      background: linear-gradient(180deg, #131c2e 0%, #0f172a 100%);
      border: 1px solid #1e293b;
      border-radius: 0.5rem;
      padding: 1rem 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
    }
    .storage-banner-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
    }
    .storage-title-area { display: flex; align-items: center; gap: 0.6rem; font-size: 0.9rem; font-weight: 600; color: #e2e8f0; }
    .storage-badges { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; font-size: 0.8rem; }
    .progress-bar-bg {
      background: #1e293b;
      border-radius: 9999px;
      height: 10px;
      width: 100%;
      overflow: hidden;
      display: flex;
    }
    .progress-bar-fill {
      height: 100%;
      transition: width 0.4s ease;
      border-radius: 9999px;
    }
    .progress-fill-normal { background: linear-gradient(90deg, #3b82f6, #10b981); }
    .progress-fill-warn { background: linear-gradient(90deg, #f59e0b, #d97706); }
    .progress-fill-crit { background: linear-gradient(90deg, #ef4444, #dc2626); }

    /* KPI Grid */
    .grid-kpi {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 1rem;
    }
    .card {
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1.15rem;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      gap: 0.5rem;
    }
    .card-title { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 700; }
    .card-value { font-size: 1.85rem; font-weight: 700; line-height: 1.2; }
    .card-subtext { font-size: 0.8rem; color: var(--text-muted); }

    /* AI Executive Summary Card */
    .ai-card {
      background: linear-gradient(180deg, #141b2d 0%, #111827 100%);
      border: 1px solid #2a3b5c;
      border-left: 4px solid var(--primary);
      border-radius: 0.5rem;
      padding: 1rem 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .ai-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      cursor: pointer;
      user-select: none;
    }
    .ai-title { font-size: 0.85rem; font-weight: 700; color: #93c5fd; display: flex; align-items: center; gap: 0.5rem; }
    .ai-body { font-size: 0.875rem; color: #e2e8f0; line-height: 1.6; }

    /* Navigation & Tabs */
    .nav-bar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
      border-bottom: 1px solid var(--border);
      padding-bottom: 0.5rem;
    }
    .main-tabs { display: flex; gap: 0.4rem; flex-wrap: wrap; }
    .main-tab {
      background: transparent;
      border: none;
      color: var(--text-muted);
      font-size: 0.9rem;
      font-weight: 600;
      padding: 0.5rem 0.9rem;
      border-radius: 0.375rem;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      gap: 0.4rem;
      transition: all 0.15s ease;
    }
    .main-tab.active {
      background-color: var(--card-bg);
      color: #fff;
      border: 1px solid var(--border);
    }
    .main-tab:hover:not(.active) { color: #fff; background-color: rgba(255, 255, 255, 0.03); }

    /* Controls: Filter & Search */
    .controls-row {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 1rem;
    }
    .filter-group { display: flex; gap: 0.4rem; flex-wrap: wrap; }
    .filter-btn {
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text-muted);
      padding: 0.35rem 0.75rem;
      border-radius: 0.375rem;
      font-size: 0.8rem;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.15s ease;
    }
    .filter-btn.active {
      background-color: var(--primary);
      color: white;
      border-color: var(--primary);
    }
    .search-box {
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.5rem 1rem;
      border-radius: 0.375rem;
      font-size: 0.85rem;
      min-width: 320px;
    }
    .search-box:focus { outline: none; border-color: var(--primary); }

    /* Tables */
    .table-container {
      background-color: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      overflow-x: auto;
    }
    table { width: 100%; border-collapse: collapse; text-align: left; font-size: 0.875rem; }
    th {
      background-color: rgba(17, 24, 39, 0.95);
      padding: 0.75rem 1rem;
      color: var(--text-muted);
      font-weight: 600;
      border-bottom: 1px solid var(--border);
      white-space: nowrap;
    }
    td { padding: 0.75rem 1rem; border-bottom: 1px solid var(--border); vertical-align: middle; }
    tr:hover { background-color: rgba(255, 255, 255, 0.02); }
    .row-clickable { cursor: pointer; }

    /* Badges */
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 0.15rem 0.55rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 700;
      text-transform: uppercase;
      white-space: nowrap;
      gap: 0.25rem;
    }
    .badge-pass { background-color: var(--pass-bg); color: var(--pass); border: 1px solid var(--pass); }
    .badge-warn { background-color: var(--warn-bg); color: var(--warn); border: 1px solid var(--warn); }
    .badge-fail { background-color: var(--fail-bg); color: var(--fail); border: 1px solid var(--fail); }
    .badge-info { background-color: rgba(59, 130, 246, 0.15); color: var(--primary); border: 1px solid var(--primary); }
    .badge-purple { background-color: var(--purple-bg); color: var(--purple); border: 1px solid var(--purple); }
    .badge-cyan { background-color: var(--cyan-bg); color: var(--cyan); border: 1px solid var(--cyan); }
    .badge-pill {
      background: #1f2937;
      border: 1px solid #374151;
      padding: 0.15rem 0.5rem;
      border-radius: 0.35rem;
      font-size: 0.75rem;
      color: #e5e7eb;
      font-family: monospace;
      margin: 0.1rem;
      display: inline-block;
    }

    /* Collapsible Details Drawer */
    .details-drawer {
      padding: 1.25rem;
      background: #0d131f;
      border-radius: 0.5rem;
      margin: 0.5rem 0;
      border: 1px solid #1f2937;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .drawer-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      gap: 1rem;
    }
    .drawer-box {
      background: #111827;
      border: 1px solid #1f2937;
      padding: 1rem;
      border-radius: 0.375rem;
    }
    .drawer-title { font-size: 0.75rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 0.5rem; font-weight: 700; }

    /* Server Card Grid */
    .server-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(340px, 1fr));
      gap: 1rem;
    }
    .server-card {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      padding: 1.25rem;
      display: flex;
      flex-direction: column;
      gap: 0.85rem;
    }
    .server-card-header { display: flex; justify-content: space-between; align-items: flex-start; }
    .server-stat-pill {
      background: #1f2937;
      border: 1px solid #374151;
      border-radius: 0.375rem;
      padding: 0.3rem 0.6rem;
      font-size: 0.8rem;
      color: #cbd5e1;
    }

    /* Interactive AI Chat Styles */
    .chat-container {
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      display: flex;
      flex-direction: column;
      height: 640px;
      overflow: hidden;
    }
    .chat-header {
      padding: 0.9rem 1.25rem;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: #131c2e;
    }
    .chat-quick-chips {
      display: flex;
      gap: 0.5rem;
      flex-wrap: wrap;
      padding: 0.75rem 1.25rem;
      background: #0d131f;
      border-bottom: 1px solid #1f2937;
    }
    .quick-chip {
      background: #1e293b;
      border: 1px solid #334155;
      color: #94a3b8;
      font-size: 0.75rem;
      padding: 0.3rem 0.75rem;
      border-radius: 9999px;
      cursor: pointer;
      transition: all 0.15s ease;
      user-select: none;
    }
    .quick-chip:hover {
      background: #334155;
      color: #fff;
      border-color: var(--primary);
    }
    .chat-messages {
      flex: 1;
      padding: 1.25rem;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 1rem;
    }
    .chat-msg {
      max-width: 82%;
      padding: 0.85rem 1.15rem;
      border-radius: 0.65rem;
      font-size: 0.875rem;
      line-height: 1.6;
      word-break: break-word;
    }
    .chat-msg-user {
      align-self: flex-end;
      background: #2563eb;
      color: white;
      border-bottom-right-radius: 0.15rem;
    }
    .chat-msg-ai {
      align-self: flex-start;
      background: #1a2234;
      border: 1px solid #28354d;
      color: #e2e8f0;
      border-bottom-left-radius: 0.15rem;
    }
    .chat-input-bar {
      padding: 1rem 1.25rem;
      border-top: 1px solid var(--border);
      display: flex;
      gap: 0.75rem;
      background: #0f172a;
    }
    .chat-input {
      flex: 1;
      background: #1e293b;
      border: 1px solid #334155;
      color: white;
      padding: 0.65rem 1rem;
      border-radius: 0.4rem;
      font-size: 0.875rem;
    }
    .chat-input:focus {
      outline: none;
      border-color: var(--primary);
    }
    .typing-indicator {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 4px;
    }
    .typing-dot {
      width: 6px;
      height: 6px;
      background: #94a3b8;
      border-radius: 50%;
      animation: pulseDot 1.4s infinite ease-in-out both;
    }
    .typing-dot:nth-child(1) { animation-delay: -0.32s; }
    .typing-dot:nth-child(2) { animation-delay: -0.16s; }
    @keyframes pulseDot {
      0%, 80%, 100% { transform: scale(0); }
      40% { transform: scale(1); }
    }

    /* Floating Chat Pill */
    .floating-chat-btn {
      position: fixed;
      bottom: 1.5rem;
      right: 1.5rem;
      background: linear-gradient(135deg, #3b82f6 0%, #8b5cf6 100%);
      color: white;
      padding: 0.7rem 1.2rem;
      border-radius: 9999px;
      font-weight: 700;
      font-size: 0.85rem;
      cursor: pointer;
      box-shadow: 0 4px 15px rgba(59, 130, 246, 0.4);
      display: flex;
      align-items: center;
      gap: 0.5rem;
      z-index: 99;
      transition: transform 0.15s ease, box-shadow 0.15s ease;
      user-select: none;
    }
    .floating-chat-btn:hover {
      transform: translateY(-2px);
      box-shadow: 0 6px 20px rgba(59, 130, 246, 0.6);
    }

    /* Folder Tree & Dropdown */
    .tree-controls {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.75rem;
      padding: 0.9rem 1.15rem;
      background: #0f172a;
      border-bottom: 1px solid var(--border);
    }
    .tree-select {
      background: var(--card-bg);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 0.45rem 0.85rem;
      border-radius: 0.375rem;
      font-size: 0.85rem;
      min-width: 260px;
      cursor: pointer;
    }
    .tree-select:focus { outline: none; border-color: var(--primary); }
    .tree-row { cursor: pointer; transition: background 0.12s ease; }
    .tree-row:hover { background-color: rgba(59, 130, 246, 0.06); }
    .tree-toggle {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 1.25rem;
      height: 1.25rem;
      font-size: 0.7rem;
      color: var(--text-muted);
      margin-right: 0.35rem;
      border-radius: 0.25rem;
      background: #1f2937;
    }
    .tree-bar-wrap {
      width: 110px;
      height: 7px;
      background: #1f2937;
      border-radius: 9999px;
      overflow: hidden;
      display: inline-block;
      vertical-align: middle;
      margin-right: 0.5rem;
    }
    .tree-bar-fill {
      height: 100%;
      background: linear-gradient(90deg, #3b82f6, #06b6d4);
      border-radius: 9999px;
    }

    /* Storage df -hT & Projection Cards */
    .storage-section-grid {
      display: flex;
      flex-direction: column;
      gap: 1.25rem;
    }
    .section-header {
      padding: 0.85rem 1.15rem;
      background: #0f172a;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    .section-title { font-size: 0.95rem; font-weight: 700; color: #f8fafc; display: flex; align-items: center; gap: 0.5rem; }
    .proj-kpi-strip {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 0.75rem;
      padding: 1rem 1.15rem;
      border-bottom: 1px solid var(--border-sub);
    }
    .proj-kpi {
      background: #0d131f;
      border: 1px solid #1f2937;
      border-radius: 0.375rem;
      padding: 0.75rem 0.9rem;
    }
    .proj-kpi-label { font-size: 0.7rem; text-transform: uppercase; color: var(--text-muted); font-weight: 700; }
    .proj-kpi-val { font-size: 1.15rem; font-weight: 700; color: #fff; margin-top: 0.2rem; }
    .svg-chart-wrap {
      padding: 1rem 1.15rem 1.25rem;
      overflow-x: auto;
    }
  </style>
</head>
<body>
  <header>
    <div class="logo-area">
      <div class="logo-icon">🛡️</div>
      <div>
        <h1>Backwatch</h1>
        <div class="subtitle">Backup Health Monitor, Storage Projection &amp; Lifecycle Management</div>
      </div>
    </div>
    <div class="header-actions">
      <span id="scan-status" style="font-size: 0.8rem; color: var(--text-muted);">Last scanned: --</span>
      <button class="btn" id="btn-scan" onclick="triggerScan()">⚡ Trigger Scan Now</button>
      <a class="btn btn-secondary" href="/metrics" target="_blank">📊 Metrics</a>
      <a class="btn btn-secondary" href="/api/report" target="_blank">📋 JSON</a>
    </div>
  </header>

  <div class="container">
    <!-- Top System & Storage Bar -->
    <div class="storage-banner">
      <div class="storage-banner-top">
        <div class="storage-title-area">
          <span>💾 Storage Mount:</span>
          <code id="mount-path" style="color: #93c5fd; font-weight: 700;">/backups</code>
          <span id="storage-badge" class="badge badge-pass">NORMAL</span>
        </div>
        <div class="storage-badges">
          <span>Total: <strong id="storage-total" style="color:#fff;">--</strong></span>
          <span>Used: <strong id="storage-used" style="color:#fff;">--</strong></span>
          <span>Free: <strong id="storage-free" style="color:#fff;">--</strong></span>
          <span class="badge badge-info" id="system-files-pill">📁 0 Helper Files (0 B)</span>
        </div>
      </div>
      <div class="progress-bar-bg">
        <div id="storage-progress" class="progress-bar-fill progress-fill-normal" style="width: 0%;"></div>
      </div>
    </div>

    <!-- AI Briefing (Collapsible) -->
    <div class="ai-card" id="ai-card-wrapper" style="display: none;">
      <div class="ai-header" onclick="toggleAI()">
        <div class="ai-title">
          <span>🤖 AI Executive Briefing</span>
          <span id="ai-status-pill" class="badge badge-purple">AI Active</span>
        </div>
        <div style="display: flex; align-items: center; gap: 0.6rem;">
          <button class="btn btn-secondary" onclick="event.stopPropagation(); switchTab('chat');" style="padding: 0.2rem 0.6rem; font-size: 0.75rem;">💬 Open Chat Assistant</button>
          <span id="ai-toggle-icon" style="color: var(--text-muted); font-size: 0.8rem;">▼</span>
        </div>
      </div>
      <div class="ai-body" id="ai-summary-text">Loading executive briefing...</div>
    </div>

    <!-- KPI Metric Tiles -->
    <div class="grid-kpi">
      <div class="card" style="border-top: 3px solid var(--primary);">
        <div class="card-title">Total Databases</div>
        <div class="card-value" id="kpi-total-dbs">0</div>
        <div class="card-subtext" id="kpi-dbs-breakdown">0 Healthy · 0 Issues</div>
      </div>
      <div class="card" style="border-top: 3px solid var(--pass);">
        <div class="card-title">Healthy (PASS)</div>
        <div class="card-value" style="color: var(--pass);" id="kpi-healthy">0</div>
        <div class="card-subtext">Within 26h SLA & Verified</div>
      </div>
      <div class="card" style="border-top: 3px solid var(--warn);">
        <div class="card-title">Warnings / Anomalies</div>
        <div class="card-value" style="color: var(--warn);" id="kpi-warnings">0</div>
        <div class="card-subtext">Size shift against baseline</div>
      </div>
      <div class="card" style="border-top: 3px solid var(--fail);">
        <div class="card-title">Failures / Stale</div>
        <div class="card-value" style="color: var(--fail);" id="kpi-failed">0</div>
        <div class="card-subtext">Missing, Stale, or Corrupt</div>
      </div>
      <div class="card" style="border-top: 3px solid var(--purple);">
        <div class="card-title">Backup Storage Volume</div>
        <div class="card-value" id="kpi-total-size" style="font-size: 1.5rem;">0 B</div>
        <div class="card-subtext" id="kpi-retention-summary">0 Retained Snapshots</div>
      </div>
      <div class="card" style="border-top: 3px solid var(--cyan);">
        <div class="card-title">DR Restore Testing</div>
        <div class="card-value" id="kpi-restore-stat" style="font-size: 1.25rem;">STANDBY</div>
        <div class="card-subtext" id="kpi-restore-sub">Automated container checks</div>
      </div>
    </div>

    <!-- Navigation Bar: 6 Tabs -->
    <div class="nav-bar">
      <div class="main-tabs">
        <button class="main-tab active" id="tab-catalog" onclick="switchTab('catalog')">🗄️ Database Catalog</button>
        <button class="main-tab" id="tab-tree" onclick="switchTab('tree')">🌳 Folder & Project Tree</button>
        <button class="main-tab" id="tab-servers" onclick="switchTab('servers')">🖥️ Server Infrastructure</button>
        <button class="main-tab" id="tab-retention" onclick="switchTab('retention')">⏳ Retention Lifecycle</button>
        <button class="main-tab" id="tab-restore" onclick="switchTab('restore')">🧪 Restore Verification</button>
        <button class="main-tab" id="tab-storage" onclick="switchTab('storage')">💾 Storage, df -hT & Projection</button>
        <button class="main-tab" id="tab-chat" onclick="switchTab('chat')">💬 AI Assistant <span class="badge badge-purple" style="font-size:0.65rem;">Interactive</span></button>
      </div>
      <div class="filter-group" id="filter-container">
        <button class="filter-btn active" id="filter-ALL" onclick="setFilter('ALL')">All</button>
        <button class="filter-btn" id="filter-FAIL" onclick="setFilter('FAIL')">Failures Only</button>
        <button class="filter-btn" id="filter-WARNING" onclick="setFilter('WARNING')">Warnings Only</button>
        <button class="filter-btn" id="filter-PASS" onclick="setFilter('PASS')">Healthy Only</button>
      </div>
    </div>

    <!-- Search Controls Row (Visible on list tabs) -->
    <div class="controls-row" id="search-controls-row">
      <input type="text" id="search-input" class="search-box" placeholder="🔍 Search database, server IP, folder date, file path..." oninput="handleSearch()">
      <div id="view-caption" style="font-size: 0.85rem; color: var(--text-muted);">Showing all monitored databases</div>
    </div>

    <!-- VIEW 1: Database Catalog (Primary Focus) -->
    <div id="view-catalog" class="table-container">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Database Name</th>
            <th>Server / Host IP</th>
            <th>Last Backup Date & Time (mtime)</th>
            <th>Actual Size</th>
            <th>Trend / Anomaly</th>
            <th>Retained Snapshots</th>
            <th>Retention Scope</th>
          </tr>
        </thead>
        <tbody id="catalog-tbody">
          <tr><td colspan="8" style="text-align:center; padding: 3rem; color: var(--text-muted);">Loading database catalog...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- VIEW 2: Categorized Folder & Project Tree -->
    <div id="view-tree" class="table-container" style="display: none;">
      <div class="tree-controls">
        <div style="display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
          <label for="tree-category-select" style="font-size: 0.85rem; color: var(--text-muted); font-weight: 600;">Category / Folder:</label>
          <select id="tree-category-select" class="tree-select" onchange="renderFolderTree()">
            <option value="ALL">All Categories & Projects</option>
          </select>
        </div>
        <div style="display: flex; gap: 0.5rem;">
          <button class="btn btn-secondary" style="font-size: 0.78rem; padding: 0.35rem 0.75rem;" onclick="expandAllTree()">⊞ Expand All</button>
          <button class="btn btn-secondary" style="font-size: 0.78rem; padding: 0.35rem 0.75rem;" onclick="collapseAllTree()">⊟ Collapse All</button>
        </div>
      </div>
      <table>
        <thead>
          <tr>
            <th>Project / Folder Hierarchy</th>
            <th>Type</th>
            <th>Health</th>
            <th>Items</th>
            <th>Consumed Space</th>
            <th>Share of Parent / Total</th>
          </tr>
        </thead>
        <tbody id="tree-tbody">
          <tr><td colspan="6" style="text-align:center; padding: 3rem; color: var(--text-muted);">Loading folder tree...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- VIEW 2B: Server Infrastructure -->
    <div id="view-servers" class="server-grid" style="display: none;"></div>

    <!-- VIEW 3: Retention Lifecycle -->
    <div id="view-retention" class="table-container" style="display: none;">
      <table>
        <thead>
          <tr>
            <th>Action</th>
            <th>Server / IP</th>
            <th>Folder Date</th>
            <th>Age (Days)</th>
            <th>Backup Groups in Snapshot</th>
            <th>Retention Status & Protection Reason</th>
          </tr>
        </thead>
        <tbody id="retention-tbody">
          <tr><td colspan="6" style="text-align:center; padding: 3rem; color: var(--text-muted);">Loading retention lifecycle audit...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- VIEW 4: Restore Verification -->
    <div id="view-restore" class="table-container" style="display: none;">
      <table>
        <thead>
          <tr>
            <th>Status</th>
            <th>Target Server</th>
            <th>Database Group</th>
            <th>Backup File</th>
            <th>Duration</th>
            <th>Verification Details</th>
          </tr>
        </thead>
        <tbody id="restore-tbody">
          <tr><td colspan="6" style="text-align:center; padding: 3rem; color: var(--text-muted);">Loading restore verification records...</td></tr>
        </tbody>
      </table>
    </div>

    <!-- VIEW 5: Storage Condition (df -hT), Projection Graph & System Files -->
    <div id="view-storage" class="storage-section-grid" style="display: none;">
      <!-- Section A: df -hT Mounted Filesystems -->
      <div class="table-container">
        <div class="section-header">
          <div class="section-title">💿 System Disk & Mount Condition (<code>df -hT</code>)</div>
          <span class="badge badge-info" id="df-mount-count">0 Mounts</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Filesystem</th>
              <th>Type</th>
              <th>Size</th>
              <th>Used</th>
              <th>Avail</th>
              <th>Use%</th>
              <th>Mounted on</th>
            </tr>
          </thead>
          <tbody id="df-tbody">
            <tr><td colspan="7" style="text-align:center; padding: 2rem; color: var(--text-muted);">Loading mounted filesystems...</td></tr>
          </tbody>
        </table>
      </div>

      <!-- Section B: Storage Projection Graph -->
      <div class="table-container">
        <div class="section-header">
          <div class="section-title">📈 Storage Growth & 30-Day Capacity Projection</div>
          <span class="badge badge-purple" id="proj-source-badge">Analyzing</span>
        </div>
        <div class="proj-kpi-strip">
          <div class="proj-kpi">
            <div class="proj-kpi-label">Current Mount Used</div>
            <div class="proj-kpi-val" id="proj-curr-used">--</div>
          </div>
          <div class="proj-kpi">
            <div class="proj-kpi-label">Daily Growth Rate</div>
            <div class="proj-kpi-val" id="proj-growth-rate">--</div>
          </div>
          <div class="proj-kpi">
            <div class="proj-kpi-label">30-Day Forecast</div>
            <div class="proj-kpi-val" id="proj-30d-val">--</div>
          </div>
          <div class="proj-kpi">
            <div class="proj-kpi-label">Est. Time Until Full</div>
            <div class="proj-kpi-val" id="proj-days-left">--</div>
          </div>
        </div>
        <div class="svg-chart-wrap" id="projection-chart-container">
          <div style="text-align:center; padding: 2rem; color: var(--text-muted);">Waiting for storage projection data...</div>
        </div>
      </div>

      <!-- Section C: System Files & Helper Scripts -->
      <div class="table-container">
        <div class="section-header">
          <div class="section-title">📁 Non-Backup System Files & Helper Scripts</div>
          <span class="badge badge-cyan" id="sysfiles-count-badge">0 Files</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>File Type</th>
              <th>File Name</th>
              <th>Relative / Absolute Path</th>
              <th>Size</th>
              <th>Last Modified (mtime)</th>
            </tr>
          </thead>
          <tbody id="sysfiles-tbody">
            <tr><td colspan="5" style="text-align:center; padding: 2.5rem; color: var(--text-muted);">Loading system files inventory...</td></tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- VIEW 6: Interactive AI Chat Assistant -->
    <div id="view-chat" class="chat-container" style="display: none;">
      <div class="chat-header">
        <div style="display: flex; align-items: center; gap: 0.6rem;">
          <span style="font-size: 1.35rem;">🤖</span>
          <div>
            <div style="font-weight: 700; color: #fff; font-size: 0.95rem;">SRE Database Reliability Copilot</div>
            <div style="font-size: 0.75rem; color: var(--text-muted);">Real-time reasoning over live backup status, storage risk & retention</div>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span id="chat-provider-badge" class="badge badge-purple">AI Active</span>
          <button class="btn btn-secondary" onclick="clearChat()" style="padding: 0.25rem 0.6rem; font-size: 0.75rem;">Clear Chat</button>
        </div>
      </div>

      <div class="chat-quick-chips">
        <span class="quick-chip" onclick="askChip('Why are backups failing and which databases are critical?')">⚡ Why are backups failing?</span>
        <span class="quick-chip" onclick="askChip('Analyze backup storage usage and identify any growth or capacity risks')">💾 Check storage & capacity risk</span>
        <span class="quick-chip" onclick="askChip('Which databases are stale or have backups older than 26 hours?')">⏳ Which backups are overdue?</span>
        <span class="quick-chip" onclick="askChip('Explain the retention policy and check if any candidates are safe to prune')">🛡️ Is retention cleanup safe?</span>
        <span class="quick-chip" onclick="askChip('Give me an executive summary of overall backup health across all servers')">📋 Overall Executive Summary</span>
      </div>

      <div class="chat-messages" id="chat-messages-box">
        <div class="chat-msg chat-msg-ai">
          👋 <strong>Hello! I am your SRE Database Reliability Copilot.</strong><br><br>
          I have live access to all monitored host servers, database snapshot histories, actual file sizes, filesystem storage capacity, and retention lifecycle rules.<br><br>
          Ask me any question or click one of the quick suggestions above!
        </div>
      </div>

      <div class="chat-input-bar">
        <input type="text" id="chat-user-input" class="chat-input" placeholder="Ask about database backups, errors, storage, or retention..." onkeydown="handleChatKey(event)">
        <button class="btn" id="chat-send-btn" onclick="sendChatFromInput()">➤ Send</button>
      </div>
    </div>

  </div>

  <footer style="text-align:center; padding: 1.5rem 1rem 0.75rem; color: var(--text-muted); font-size: 0.8rem; border-top: 1px solid var(--border-sub); max-width: 1540px; margin: 1.5rem auto 0;">
    <span>🛡️ <strong>Backwatch</strong> — Automated Backup Health &amp; Retention Monitor</span>
    <span style="margin: 0 0.5rem;">·</span>
    <a href="https://github.com/cyraxabir/BackWatch" target="_blank" rel="noopener noreferrer" style="color: #93c5fd; text-decoration: none;">GitHub: https://github.com/cyraxabir/BackWatch</a>
  </footer>
  <div class="floating-chat-btn" onclick="switchTab('chat')" title="Ask AI SRE Copilot">
    <span>🤖</span>
    <span>Ask AI Copilot</span>
  </div>

  <script>
    let reportData = null;
    let currentTab = 'catalog';
    let currentFilter = 'ALL';
    let expandedRows = new Set();
    let aiExpanded = true;

    function formatBytes(bytes) {
      if (!bytes || bytes === 0) return '0 B';
      const k = 1024;
      const sizes = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
      const i = Math.floor(Math.log(bytes) / Math.log(k));
      return (bytes / Math.pow(k, i)).toFixed(2) + ' ' + sizes[i];
    }

    function timeAgo(dateStr) {
      if (!dateStr) return 'N/A';
      try {
        const d = new Date(dateStr);
        const now = new Date();
        const diffHrs = (now - d) / 3600000;
        if (diffHrs < 1) return Math.max(1, Math.round(diffHrs * 60)) + 'm ago';
        if (diffHrs < 24) return Math.round(diffHrs) + 'h ago';
        return (diffHrs / 24).toFixed(1) + 'd ago';
      } catch (e) {
        return dateStr;
      }
    }

    async function loadData() {
      try {
        const resp = await fetch('/api/report');
        if (!resp.ok) throw new Error('API returned ' + resp.status);
        reportData = await resp.json();
        renderDashboard();
      } catch (e) {
        console.error('Failed to load report:', e);
      }
    }

    async function loadAISummary() {
      try {
        const resp = await fetch('/api/ai-summary');
        if (resp.ok) {
          const txt = await resp.text();
          const card = document.getElementById('ai-card-wrapper');
          if (txt && txt.trim().length > 0) {
            document.getElementById('ai-summary-text').innerText = txt.trim();
            card.style.display = 'flex';
          } else {
            card.style.display = 'none';
          }
        }
      } catch (e) {
        console.warn('AI summary fetch failed:', e);
      }
    }

    function toggleAI() {
      aiExpanded = !aiExpanded;
      document.getElementById('ai-summary-text').style.display = aiExpanded ? 'block' : 'none';
      document.getElementById('ai-toggle-icon').innerText = aiExpanded ? '▼' : '▶';
    }

    function switchTab(tab) {
      currentTab = tab;
      ['catalog', 'tree', 'servers', 'retention', 'restore', 'storage', 'chat'].forEach(t => {
        const tabEl = document.getElementById('tab-' + t);
        const viewEl = document.getElementById('view-' + t);
        if (tabEl) tabEl.classList.toggle('active', t === tab);
        if (viewEl) viewEl.style.display = (t === tab) ? (t === 'servers' ? 'grid' : (t === 'chat' ? 'flex' : (t === 'storage' ? 'flex' : 'block'))) : 'none';
      });

      // Show/hide filter and search controls depending on tab
      const filterCont = document.getElementById('filter-container');
      const searchRow = document.getElementById('search-controls-row');
      if (filterCont) filterCont.style.display = (tab === 'catalog' || tab === 'servers') ? 'flex' : 'none';
      if (searchRow) searchRow.style.display = (tab === 'chat') ? 'none' : 'flex';

      if (tab === 'chat') {
        const inp = document.getElementById('chat-user-input');
        if (inp) setTimeout(() => inp.focus(), 100);
      } else {
        renderActiveView();
      }
    }

    function setFilter(filter) {
      currentFilter = filter;
      ['ALL', 'FAIL', 'WARNING', 'PASS'].forEach(f => {
        const btn = document.getElementById('filter-' + f);
        if (btn) btn.classList.toggle('active', f === filter);
      });
      renderActiveView();
    }

    function handleSearch() {
      renderActiveView();
    }

    function renderDashboard() {
      if (!reportData) return;

      // 1. Scan status header
      if (reportData.timestamp) {
        const scanDt = reportData.timestamp.replace('T', ' ').substring(0, 19);
        document.getElementById('scan-status').innerText = 'Last scanned: ' + scanDt + ' (' + timeAgo(reportData.timestamp) + ')';
      }

      // 2. Storage Banner
      const storage = reportData.system_storage || {};
      const totalBytes = storage.total_bytes || 0;
      const usedBytes = storage.used_bytes || 0;
      const freeBytes = storage.free_bytes || 0;
      const pct = storage.percent_used || 0.0;

      document.getElementById('mount-path').innerText = storage.mount_point || '/backups';
      document.getElementById('storage-total').innerText = formatBytes(totalBytes);
      document.getElementById('storage-used').innerText = formatBytes(usedBytes) + ' (' + pct + '%)';
      document.getElementById('storage-free').innerText = formatBytes(freeBytes);

      const bar = document.getElementById('storage-progress');
      bar.style.width = Math.min(100, Math.max(0, pct)) + '%';
      bar.className = 'progress-bar-fill ' + (pct > 90 ? 'progress-fill-crit' : (pct > 80 ? 'progress-fill-warn' : 'progress-fill-normal'));

      const sBadge = document.getElementById('storage-badge');
      if (pct > 90) { sBadge.className = 'badge badge-fail'; sBadge.innerText = 'STORAGE CRITICAL'; }
      else if (pct > 80) { sBadge.className = 'badge badge-warn'; sBadge.innerText = 'STORAGE WARNING'; }
      else { sBadge.className = 'badge badge-pass'; sBadge.innerText = 'NORMAL'; }

      // System files pill
      const sysFiles = reportData.system_files || {};
      document.getElementById('system-files-pill').innerText = '📁 ' + (sysFiles.total_count || 0) + ' Helper Files (' + formatBytes(sysFiles.total_bytes || 0) + ')';

      // 3. KPIs
      const summary = reportData.summary || {};
      const dbs = reportData.databases || [];
      const totalDbs = dbs.length || summary.total_databases || 0;
      const healthyDbs = summary.healthy_databases !== undefined ? summary.healthy_databases : summary.healthy || 0;
      const warningDbs = summary.warning_databases !== undefined ? summary.warning_databases : summary.warning || 0;
      const failedDbs = summary.failed_databases !== undefined ? summary.failed_databases : summary.failed || 0;

      document.getElementById('kpi-total-dbs').innerText = totalDbs;
      document.getElementById('kpi-dbs-breakdown').innerText = `${healthyDbs} Healthy · ${failedDbs} Issues · ${warningDbs} Warn`;
      document.getElementById('kpi-healthy').innerText = healthyDbs;
      document.getElementById('kpi-warnings').innerText = warningDbs;
      document.getElementById('kpi-failed').innerText = failedDbs;

      // Calculate Total Active Size vs Total Retained Size
      let totalActiveBytes = 0;
      let totalRetainedSnapshots = 0;
      dbs.forEach(d => {
        totalActiveBytes += (d.actual_size_bytes || 0);
        totalRetainedSnapshots += (d.retained_count || 1);
      });
      document.getElementById('kpi-total-size').innerText = formatBytes(totalActiveBytes);
      document.getElementById('kpi-retention-summary').innerText = `${totalRetainedSnapshots} Retained Historical Snapshots`;

      // Restore testing status
      const restores = reportData.restore_results || [];
      const restoreKpi = document.getElementById('kpi-restore-stat');
      const restoreSub = document.getElementById('kpi-restore-sub');
      if (restores.length > 0) {
        const failCount = restores.filter(r => !r.success).length;
        if (failCount > 0) {
          restoreKpi.innerText = 'FAIL (' + failCount + ')';
          restoreKpi.style.color = 'var(--fail)';
        } else {
          restoreKpi.innerText = 'PASSED';
          restoreKpi.style.color = 'var(--pass)';
        }
        restoreSub.innerText = `${restores.length} container tests verified`;
      } else {
        restoreKpi.innerText = 'STANDBY';
        restoreKpi.style.color = 'var(--cyan)';
        restoreSub.innerText = 'Dry-run integrity checked';
      }

      renderActiveView();
      populateTreeDropdown();
    }

    function renderActiveView() {
      if (currentTab === 'catalog') renderCatalog();
      else if (currentTab === 'tree') renderFolderTree();
      else if (currentTab === 'servers') renderServers();
      else if (currentTab === 'retention') renderRetention();
      else if (currentTab === 'restore') renderRestore();
      else if (currentTab === 'storage') renderStorage();
    }

    // ── VIEW 1: Database Catalog ───────────────────────────────────────
    function renderCatalog() {
      const tbody = document.getElementById('catalog-tbody');
      tbody.innerHTML = '';
      const dbs = reportData.databases || [];
      const query = (document.getElementById('search-input').value || '').toLowerCase();

      const filtered = dbs.filter(d => {
        if (currentFilter !== 'ALL' && d.health !== currentFilter) return false;
        if (query) {
          const match = (d.database || '').toLowerCase().includes(query) ||
                        (d.server || '').toLowerCase().includes(query) ||
                        (d.latest_backup_file || '').toLowerCase().includes(query) ||
                        (d.latest_folder_date || '').toLowerCase().includes(query);
          if (!match) return false;
        }
        return true;
      });

      document.getElementById('view-caption').innerText = `Showing ${filtered.length} of ${dbs.length} databases`;

      if (filtered.length === 0) {
        tbody.innerHTML = '<tr><td colspan="8" style="text-align:center; padding: 3rem; color: var(--text-muted);">No databases match the filter.</td></tr>';
        return;
      }

      filtered.forEach((d, idx) => {
        const rowId = `db-${d.server}-${d.database}`.replace(/[^a-zA-Z0-9_-]/g, '_');
        const isExpanded = expandedRows.has(rowId);

        let badgeClass = 'badge-pass';
        let badgeIcon = '✓';
        if (d.health === 'FAIL') { badgeClass = 'badge-fail'; badgeIcon = '✕'; }
        else if (d.health === 'WARNING') { badgeClass = 'badge-warn'; badgeIcon = '⚠'; }

        // Anomaly / Trend
        let trendBadge = '<span style="color: var(--text-muted); font-size: 0.8rem;">Normal</span>';
        if (d.size_anomaly) {
          trendBadge = `<span class="badge badge-warn" title="${d.size_anomaly_detail || 'Size anomaly'}">⚠ Anomaly</span>`;
        }

        const tr = document.createElement('tr');
        tr.className = 'row-clickable';
        tr.onclick = (e) => toggleDrawer(rowId, e);
        tr.innerHTML = `
          <td><span class="badge ${badgeClass}">${badgeIcon} ${d.health}</span></td>
          <td>
            <div style="font-weight: 700; color: #fff; font-size: 0.95rem;">${d.database}</div>
            <div style="font-size: 0.75rem; color: var(--text-muted); font-family: monospace;">${d.latest_backup_file || 'No files'}</div>
          </td>
          <td><span class="badge-pill">🖥️ ${d.server}</span></td>
          <td>
            <div style="font-weight: 600; color: #e2e8f0;">${d.latest_folder_date || 'N/A'}</div>
            <div style="font-size: 0.75rem; color: var(--text-muted);">${d.latest_mtime ? d.latest_mtime.replace('T', ' ').substring(0, 16) + ' (' + timeAgo(d.latest_mtime) + ')' : 'N/A'}</div>
          </td>
          <td>
            <div style="font-weight: 700; color: #fff;">${formatBytes(d.actual_size_bytes)}</div>
            <div style="font-size: 0.75rem; color: var(--text-muted);">${d.file_count || 0} file(s)</div>
          </td>
          <td>${trendBadge}</td>
          <td><span class="badge-pill" style="font-weight: 700; color: #a5b4fc;">${d.retained_count || 1} copies</span></td>
          <td>
            <span class="badge badge-purple" style="font-size: 0.7rem;">7-Day Window</span>
            <span style="font-size: 0.8rem; color: var(--text-muted); margin-left: 0.25rem;">${isExpanded ? '▲' : '▼'}</span>
          </td>
        `;
        tbody.appendChild(tr);

        // Expanded Drawer
        if (isExpanded) {
          const drawerTr = document.createElement('tr');
          drawerTr.innerHTML = `
            <td colspan="8" style="padding: 0; background-color: #0b0f19;">
              <div class="details-drawer">
                <div class="drawer-grid">
                  <div class="drawer-box">
                    <div class="drawer-title">Active Snapshot Details</div>
                    <div style="font-size: 0.8rem; line-height: 1.8;">
                      <div><strong>Server:</strong> ${d.server}</div>
                      <div><strong>Database:</strong> ${d.database}</div>
                      <div><strong>Latest Folder:</strong> <code>${d.latest_folder_date || 'N/A'}</code></div>
                      <div><strong>Actual File mtime:</strong> <code>${d.latest_mtime || 'N/A'}</code></div>
                      <div><strong>Inode ctime:</strong> <code>${d.latest_ctime || 'N/A'}</code></div>
                      <div><strong>Age:</strong> ${d.age_hours !== null ? d.age_hours + ' hours' : 'N/A'}</div>
                      <div><strong>Integrity Check:</strong> <span class="badge badge-${d.integrity === 'PASS' ? 'pass' : 'warn'}">${d.integrity}</span></div>
                    </div>
                  </div>

                  <div class="drawer-box">
                    <div class="drawer-title">Retained Snapshots on Disk (${d.retained_count || 1})</div>
                    <div style="display: flex; flex-wrap: wrap; gap: 0.4rem; max-height: 140px; overflow-y: auto;">
                      ${(d.retained_snapshots || []).map(s => {
                        const hCol = s.health === 'PASS' ? 'var(--pass)' : 'var(--fail)';
                        return `<span class="badge-pill" style="border-left: 3px solid ${hCol};">
                          <strong>${s.folder_date}</strong> (${formatBytes(s.size_bytes)})
                        </span>`;
                      }).join('')}
                    </div>
                    ${d.last_successful_date && d.last_successful_date !== d.latest_folder_date ? `
                      <div style="margin-top: 0.75rem; font-size: 0.75rem; color: #10b981;">
                        ✓ Last known successful backup: <strong>${d.last_successful_date}</strong> (${formatBytes(d.last_successful_size_bytes)})
                      </div>
                    ` : ''}
                  </div>
                </div>

                ${(d.errors && d.errors.length > 0) || (d.warnings && d.warnings.length > 0) ? `
                  <div style="background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 0.375rem; padding: 0.75rem;">
                    <div style="color: #ef4444; font-weight: 700; font-size: 0.8rem; margin-bottom: 0.25rem;">Diagnostics & Failures:</div>
                    ${(d.errors || []).map(e => `<div style="color: #fca5a5; font-size: 0.8rem;">❌ ${e}</div>`).join('')}
                    ${(d.warnings || []).map(w => `<div style="color: #fde68a; font-size: 0.8rem;">⚠️ ${w}</div>`).join('')}
                  </div>
                ` : ''}
              </div>
            </td>
          `;
          tbody.appendChild(drawerTr);
        }
      });
    }

    function toggleDrawer(rowId, event) {
      if (expandedRows.has(rowId)) expandedRows.delete(rowId);
      else expandedRows.add(rowId);
      renderCatalog();
    }

    // ── VIEW 2: Server Infrastructure ─────────────────────────────────
    function renderServers() {
      const container = document.getElementById('view-servers');
      container.innerHTML = '';
      const dbs = reportData.databases || [];
      const query = (document.getElementById('search-input').value || '').toLowerCase();

      const serverMap = {};
      dbs.forEach(d => {
        if (!serverMap[d.server]) {
          serverMap[d.server] = {
            server: d.server,
            databases: [],
            totalBytes: 0,
            hasFail: false,
            hasWarn: false,
            latestMtime: null,
            dates: new Set(),
          };
        }
        const s = serverMap[d.server];
        s.databases.push(d);
        s.totalBytes += (d.actual_size_bytes || 0);
        if (d.health === 'FAIL') s.hasFail = true;
        if (d.health === 'WARNING') s.hasWarn = true;
        if (d.latest_mtime && (!s.latestMtime || d.latest_mtime > s.latestMtime)) {
          s.latestMtime = d.latest_mtime;
        }
        (d.retained_dates || []).forEach(dt => s.dates.add(dt));
      });

      const servers = Object.values(serverMap).sort((a, b) => a.server.localeCompare(b.server));
      const filtered = servers.filter(s => {
        if (currentFilter === 'FAIL' && !s.hasFail) return false;
        if (currentFilter === 'PASS' && s.hasFail) return false;
        if (currentFilter === 'WARNING' && !s.hasWarn) return false;
        if (query && !s.server.toLowerCase().includes(query)) {
          const matchDb = s.databases.some(d => d.database.toLowerCase().includes(query));
          if (!matchDb) return false;
        }
        return true;
      });

      if (filtered.length === 0) {
        container.innerHTML = '<div style="grid-column: 1 / -1; text-align: center; padding: 3rem; color: var(--text-muted);">No servers match the filter.</div>';
        return;
      }

      filtered.forEach(s => {
        let badge = '<span class="badge badge-pass">HEALTHY</span>';
        if (s.hasFail) badge = '<span class="badge badge-fail">ISSUES DETECTED</span>';
        else if (s.hasWarn) badge = '<span class="badge badge-warn">WARNING</span>';

        const card = document.createElement('div');
        card.className = 'server-card';
        card.innerHTML = `
          <div class="server-card-header">
            <div>
              <h3 style="font-size: 1.1rem; font-weight: 700; color: #fff;">🖥️ ${s.server}</h3>
              <div style="font-size: 0.8rem; color: var(--text-muted); margin-top: 0.15rem;">
                Last backup: ${s.latestMtime ? s.latestMtime.replace('T', ' ').substring(0, 16) : 'N/A'}
              </div>
            </div>
            ${badge}
          </div>

          <div style="display: flex; gap: 0.5rem; flex-wrap: wrap;">
            <span class="server-stat-pill"><strong>${s.databases.length}</strong> Databases</span>
            <span class="server-stat-pill"><strong>${formatBytes(s.totalBytes)}</strong> Active Size</span>
            <span class="server-stat-pill"><strong>${s.dates.size}</strong> Retained Dates</span>
          </div>

          <div style="background: #0d131f; border: 1px solid #1f2937; border-radius: 0.375rem; padding: 0.75rem; max-height: 160px; overflow-y: auto;">
            <div style="font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.35rem; font-weight: 600;">Hosted Databases:</div>
            <div style="display: flex; flex-wrap: wrap; gap: 0.25rem;">
              ${s.databases.map(d => {
                const bCol = d.health === 'PASS' ? 'var(--pass)' : (d.health === 'FAIL' ? 'var(--fail)' : 'var(--warn)');
                return `<span class="badge-pill" style="border-left: 2px solid ${bCol};">${d.database}</span>`;
              }).join('')}
            </div>
          </div>
        `;
        container.appendChild(card);
      });
    }

    // ── VIEW 3: Retention Lifecycle ───────────────────────────────────
    function renderRetention() {
      const tbody = document.getElementById('retention-tbody');
      tbody.innerHTML = '';
      const candidates = reportData.retention_candidates || [];

      if (candidates.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 2.5rem; color: var(--text-muted);">No expired retention candidates. All backup directories are within the 7-day retention window or actively protected.</td></tr>';
        return;
      }

      candidates.forEach(c => {
        const tr = document.createElement('tr');
        const badge = c.safe_to_delete
          ? '<span class="badge badge-warn">DRY RUN: Would Delete</span>'
          : '<span class="badge badge-fail">BLOCKED (Protected)</span>';

        tr.innerHTML = `
          <td>${badge}</td>
          <td><strong>${c.server}</strong></td>
          <td><code style="font-weight: 700; color: #c7d2fe;">${c.folder_date}</code></td>
          <td>${c.age_days ? c.age_days.toFixed(1) + ' days' : 'N/A'}</td>
          <td>${(c.backup_groups || []).map(g => `<span class="badge-pill">${g}</span>`).join('')}</td>
          <td>
            ${c.block_reason
              ? `<span style="color: #f87171; font-weight: 500;">🔒 ${c.block_reason}</span>`
              : '<span style="color: #fbbf24;">Eligible for deletion (>7d SLA). Currently dry-run protected.</span>'}
          </td>
        `;
        tbody.appendChild(tr);
      });
    }

    // ── VIEW 4: Restore Verification ──────────────────────────────────
    function renderRestore() {
      const tbody = document.getElementById('restore-tbody');
      tbody.innerHTML = '';
      const results = reportData.restore_results || [];

      if (results.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6" style="text-align:center; padding: 3rem; color: var(--text-muted);">
              <div style="font-size: 1.1rem; font-weight: 600; color: #e2e8f0; margin-bottom: 0.5rem;">Automated Restore Testing Standby</div>
              <div style="max-width: 600px; margin: 0 auto; font-size: 0.85rem; line-height: 1.6;">
                Filesystem integrity checks (gzip header validation) are active. To enable automated database restoration tests into disposable Docker containers, set <code>restore_test.enabled: true</code> in your configuration.
              </div>
            </td>
          </tr>
        `;
        return;
      }

      results.forEach(r => {
        const tr = document.createElement('tr');
        const badge = r.success
          ? '<span class="badge badge-pass">PASSED</span>'
          : '<span class="badge badge-fail">FAILED</span>';

        tr.innerHTML = `
          <td>${badge}</td>
          <td>${r.server}</td>
          <td><strong>${r.group}</strong></td>
          <td><code style="font-size: 0.75rem;">${r.file}</code></td>
          <td>${r.duration_seconds ? r.duration_seconds + 's' : 'N/A'}</td>
          <td><span style="font-size: 0.8rem; color: ${r.success ? '#94a3b8' : '#f87171'};">${r.message}</span></td>
        `;
        tbody.appendChild(tr);
      });
    }

    // ── VIEW 2A: Categorized Folder & Project Tree ────────────────────
    let expandedTreeNodes = new Set();
    let treeInitialized = false;

    function populateTreeDropdown() {
      const sel = document.getElementById('tree-category-select');
      if (!sel || !reportData) return;
      const prevVal = sel.value || 'ALL';
      const tree = reportData.folder_tree || [];

      let opts = '<option value="ALL">All Categories & Projects</option>';
      tree.forEach(cat => {
        opts += `<option value="CAT:${cat.name}">🗂️ ${cat.name} (${formatBytes(cat.size_bytes)})</option>`;
        (cat.children || []).forEach(srv => {
          opts += `<option value="SRV:${cat.name}::${srv.name}">&nbsp;&nbsp;↳ 📁 ${srv.name} (${formatBytes(srv.size_bytes)})</option>`;
        });
      });
      sel.innerHTML = opts;
      if ([...sel.options].some(o => o.value === prevVal)) {
        sel.value = prevVal;
      }
      if (!treeInitialized && tree.length > 0) {
        tree.forEach(cat => expandedTreeNodes.add('cat-' + cat.name));
        treeInitialized = true;
      }
    }

    function toggleTreeNode(nodeId) {
      if (expandedTreeNodes.has(nodeId)) expandedTreeNodes.delete(nodeId);
      else expandedTreeNodes.add(nodeId);
      renderFolderTree();
    }

    function expandAllTree() {
      const tree = reportData.folder_tree || [];
      function _collect(nodes, prefix) {
        (nodes || []).forEach(n => {
          const id = (prefix + '-' + n.node_type + '-' + n.name).replace(/[^a-zA-Z0-9_-]/g, '_');
          if (n.children && n.children.length > 0) {
            expandedTreeNodes.add(id);
            _collect(n.children, id);
          }
        });
      }
      _collect(tree, 'root');
      renderFolderTree();
    }

    function collapseAllTree() {
      expandedTreeNodes.clear();
      renderFolderTree();
    }

    function renderFolderTree() {
      const tbody = document.getElementById('tree-tbody');
      if (!tbody) return;
      tbody.innerHTML = '';
      const tree = reportData.folder_tree || [];
      const selVal = (document.getElementById('tree-category-select') || {}).value || 'ALL';
      const query = (document.getElementById('search-input').value || '').toLowerCase();

      let roots = tree;
      if (selVal.startsWith('CAT:')) {
        const catName = selVal.slice(4);
        roots = tree.filter(c => c.name === catName);
      } else if (selVal.startsWith('SRV:')) {
        const parts = selVal.slice(4).split('::');
        const catName = parts[0];
        const srvName = parts[1];
        const cat = tree.find(c => c.name === catName);
        if (cat) {
          roots = (cat.children || []).filter(s => s.name === srvName);
        } else {
          roots = [];
        }
      }

      if (roots.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center; padding: 3rem; color: var(--text-muted);">No folders or projects found.</td></tr>';
        return;
      }

      let renderedCount = 0;

      function _nodeMatches(node) {
        if (!query) return true;
        if ((node.name || '').toLowerCase().includes(query)) return true;
        if ((node.path || '').toLowerCase().includes(query)) return true;
        return (node.children || []).some(c => _nodeMatches(c));
      }

      function _appendNode(node, depth, prefix) {
        if (!_nodeMatches(node)) return;
        const nodeId = (node.node_type === 'category' && prefix === 'root')
          ? ('cat-' + node.name)
          : (prefix + '-' + node.node_type + '-' + node.name).replace(/[^a-zA-Z0-9_-]/g, '_');

        const hasChildren = node.children && node.children.length > 0;
        const isExpanded = expandedTreeNodes.has(nodeId) || (query.length > 0 && hasChildren);

        const iconMap = {
          category: '🗂️',
          server: '🖥️',
          project: '🗄️',
          snapshot: '📅',
          file: '📄',
        };
        const typeLabelMap = {
          category: '<span class="badge badge-purple">CATEGORY</span>',
          server: '<span class="badge badge-info">FOLDER / HOST</span>',
          project: '<span class="badge badge-cyan">PROJECT / DB</span>',
          snapshot: '<span class="badge-pill">SNAPSHOT</span>',
          file: '<span style="color:var(--text-muted); font-size:0.75rem;">FILE</span>',
        };

        let hBadge = '<span class="badge badge-pass">PASS</span>';
        if (node.health === 'FAIL') hBadge = '<span class="badge badge-fail">FAIL</span>';
        else if (node.health === 'WARNING') hBadge = '<span class="badge badge-warn">WARN</span>';
        else if (node.health === 'UNKNOWN') hBadge = '<span class="badge-pill">UNKNOWN</span>';

        const indentPx = depth * 22;
        const toggleHtml = hasChildren
          ? `<span class="tree-toggle">${isExpanded ? '▼' : '▶'}</span>`
          : `<span style="display:inline-block; width:1.6rem;"></span>`;

        const tr = document.createElement('tr');
        tr.className = hasChildren ? 'tree-row' : '';
        if (hasChildren) tr.onclick = () => toggleTreeNode(nodeId);

        const pctParent = node.pct_of_parent !== undefined ? node.pct_of_parent : node.pct_of_total;
        tr.innerHTML = `
          <td>
            <div style="padding-left: ${indentPx}px; display: flex; align-items: center;">
              ${toggleHtml}
              <span style="margin-right: 0.45rem;">${iconMap[node.node_type] || '📁'}</span>
              <div>
                <span style="font-weight: ${depth <= 2 ? '700' : '500'}; color: ${node.node_type === 'file' ? '#cbd5e1' : '#fff'};">${node.name}</span>
                ${node.node_type === 'file' && node.mtime ? `<span style="font-size:0.72rem; color:var(--text-muted); margin-left:0.5rem;">(${node.mtime.replace('T',' ').substring(0,16)})</span>` : ''}
              </div>
            </div>
          </td>
          <td>${typeLabelMap[node.node_type] || node.node_type}</td>
          <td>${hBadge}</td>
          <td style="color: var(--text-muted); font-size: 0.8rem;">${node.node_type === 'file' ? '1 file' : (node.item_count + ' items')}</td>
          <td><strong style="color: #fff;">${formatBytes(node.size_bytes)}</strong></td>
          <td>
            <div class="tree-bar-wrap"><div class="tree-bar-fill" style="width: ${Math.min(100, Math.max(0, pctParent || 0))}%;"></div></div>
            <span style="font-size: 0.78rem; color: #cbd5e1;">${pctParent || 0}%</span>
            <span style="font-size: 0.72rem; color: var(--text-muted);">(${node.pct_of_total || 0}% total)</span>
          </td>
        `;
        tbody.appendChild(tr);
        renderedCount++;

        if (hasChildren && isExpanded) {
          node.children.forEach(ch => _appendNode(ch, depth + 1, nodeId));
        }
      }

      roots.forEach(r => _appendNode(r, 0, 'root'));
      document.getElementById('view-caption').innerText = `Showing ${renderedCount} visible tree nodes`;
    }

    // ── VIEW 5: Storage Condition (df -hT), Projection & System Files ─
    function renderStorage() {
      // A. df -hT Mounts
      const dfTbody = document.getElementById('df-tbody');
      const mounts = reportData.df_mounts || [];
      document.getElementById('df-mount-count').innerText = `${mounts.length} Mount(s)`;
      if (dfTbody) {
        dfTbody.innerHTML = '';
        if (mounts.length === 0) {
          dfTbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding: 2rem; color: var(--text-muted);">No mounted filesystem data available.</td></tr>';
        } else {
          mounts.forEach(m => {
            const pct = m.percent_used || 0;
            const fillCls = pct > 90 ? 'progress-fill-crit' : (pct > 80 ? 'progress-fill-warn' : 'progress-fill-normal');
            const tags = [];
            if (m.is_backup_mount) tags.push('<span class="badge badge-info" style="font-size:0.65rem;">BACKUP ROOT</span>');
            if (m.is_state_mount) tags.push('<span class="badge badge-purple" style="font-size:0.65rem;">STATE DIR</span>');

            const tr = document.createElement('tr');
            tr.innerHTML = `
              <td><code style="color:#e2e8f0; font-weight:600;">${m.filesystem}</code></td>
              <td><span class="badge-pill">${m.fs_type}</span></td>
              <td><strong>${formatBytes(m.size_bytes)}</strong></td>
              <td>${formatBytes(m.used_bytes)}</td>
              <td style="color:#10b981;">${formatBytes(m.avail_bytes)}</td>
              <td>
                <div style="display:flex; align-items:center; gap:0.5rem;">
                  <div class="tree-bar-wrap" style="width:90px;"><div class="progress-bar-fill ${fillCls}" style="width:${Math.min(100, pct)}%;"></div></div>
                  <strong style="color:${pct > 90 ? 'var(--fail)' : (pct > 80 ? 'var(--warn)' : '#fff')};">${pct}%</strong>
                </div>
              </td>
              <td><code style="color:#93c5fd;">${m.mounted_on}</code> ${tags.join(' ')}</td>
            `;
            dfTbody.appendChild(tr);
          });
        }
      }

      // B. Storage Projection Graph
      renderProjectionGraph();

      // C. System Files
      const tbody = document.getElementById('sysfiles-tbody');
      tbody.innerHTML = '';
      const sysFiles = reportData.system_files || {};
      const samples = sysFiles.sample_files || [];
      const countBadge = document.getElementById('sysfiles-count-badge');
      if (countBadge) countBadge.innerText = `${sysFiles.total_count || 0} Files (${formatBytes(sysFiles.total_bytes || 0)})`;

      if (samples.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding: 2.5rem; color: var(--text-muted);">No non-backup helper files or scripts discovered in the backup directory tree.</td></tr>';
        return;
      }

      samples.forEach(f => {
        const tr = document.createElement('tr');
        let typeBadge = '<span class="badge badge-info">FILE</span>';
        if (f.extension === '.sh' || f.extension === '.bash') typeBadge = '<span class="badge badge-purple">SCRIPT</span>';
        else if (f.extension === '.log') typeBadge = '<span class="badge badge-warn">LOG</span>';
        else if (f.extension === '.txt' || f.extension === '.md') typeBadge = '<span class="badge badge-info">DOC</span>';

        tr.innerHTML = `
          <td>${typeBadge}</td>
          <td><strong style="color: #fff;">${f.filename}</strong></td>
          <td><code style="font-size: 0.75rem; color: #94a3b8;">${f.path}</code></td>
          <td>${formatBytes(f.size_bytes)}</td>
          <td style="font-size: 0.75rem; color: var(--text-muted);">${f.mtime ? f.mtime.replace('T', ' ').substring(0, 16) : 'N/A'}</td>
        `;
        tbody.appendChild(tr);
      });
    }

    function renderProjectionGraph() {
      const proj = reportData.storage_projection || {};
      const cont = document.getElementById('projection-chart-container');
      if (!cont) return;

      const totalB = proj.total_bytes || 1;
      const usedB = proj.used_bytes || 0;
      const growthB = proj.daily_growth_bytes || 0;
      const forecast = proj.forecast || [];
      const history = proj.history || [];

      document.getElementById('proj-curr-used').innerText = formatBytes(usedB);
      const sign = growthB > 0 ? '+' : '';
      document.getElementById('proj-growth-rate').innerText = `${sign}${formatBytes(Math.abs(growthB))}/day`;
      document.getElementById('proj-growth-rate').style.color = growthB > 0 ? '#fbbf24' : '#10b981';
      if (growthB < 0) document.getElementById('proj-growth-rate').innerText = `-${formatBytes(Math.abs(growthB))}/day`;

      const lastForecast = forecast.length > 0 ? forecast[forecast.length - 1].projected_bytes : usedB;
      document.getElementById('proj-30d-val').innerText = formatBytes(lastForecast);

      const daysLeftEl = document.getElementById('proj-days-left');
      if (proj.days_until_full) {
        daysLeftEl.innerText = `${proj.days_until_full} days (${proj.projected_full_date || ''})`;
        daysLeftEl.style.color = proj.days_until_full < 30 ? 'var(--fail)' : 'var(--warn)';
      } else {
        daysLeftEl.innerText = proj.status === 'reclaiming' ? 'Reclaiming Space' : 'Stable (No Exhaustion)';
        daysLeftEl.style.color = 'var(--pass)';
      }

      const srcBadge = document.getElementById('proj-source-badge');
      if (srcBadge) {
        const srcMap = {
          storage_history: 'Live SQLite History',
          disk_snapshots: 'On-Disk Snapshot Trend',
          current_only: 'Baseline Snapshot',
        };
        srcBadge.innerText = srcMap[proj.source] || 'Analyzing';
      }

      const allPts = [
        ...history.map(h => ({ date: h.date, bytes: h.used_bytes, kind: 'actual' })),
        ...forecast.map(f => ({ date: f.date, bytes: f.projected_bytes, kind: 'forecast' })),
      ];

      if (allPts.length === 0) {
        cont.innerHTML = '<div style="text-align:center; padding: 2rem; color: var(--text-muted);">No storage projection points available yet.</div>';
        return;
      }

      const W = 860, H = 240;
      const padL = 72, padR = 24, padT = 20, padB = 34;
      const plotW = W - padL - padR;
      const plotH = H - padT - padB;
      const maxY = Math.max(totalB, ...allPts.map(p => p.bytes), 1);

      const xAt = (idx) => allPts.length === 1 ? padL + plotW / 2 : padL + (idx / (allPts.length - 1)) * plotW;
      const yAt = (val) => padT + plotH - (Math.min(maxY, Math.max(0, val)) / maxY) * plotH;

      const actualPts = [];
      const forecastPts = [];
      allPts.forEach((p, idx) => {
        const xy = `${xAt(idx).toFixed(1)},${yAt(p.bytes).toFixed(1)}`;
        if (p.kind === 'actual') actualPts.push(xy);
        else forecastPts.push(xy);
      });
      if (actualPts.length > 0 && forecastPts.length > 0) {
        forecastPts.unshift(actualPts[actualPts.length - 1]);
      }

      const y100 = yAt(totalB).toFixed(1);
      const y80 = yAt(totalB * 0.8).toFixed(1);
      const y50 = yAt(totalB * 0.5).toFixed(1);

      const circles = allPts.map((p, idx) => {
        const cx = xAt(idx).toFixed(1);
        const cy = yAt(p.bytes).toFixed(1);
        const col = p.kind === 'actual' ? '#38bdf8' : '#a78bfa';
        return `<circle cx="${cx}" cy="${cy}" r="4" fill="${col}" stroke="#0b0f19" stroke-width="1.5">
                  <title>${p.date} (${p.kind === 'actual' ? 'Actual' : 'Projected'}): ${formatBytes(p.bytes)}</title>
                </circle>`;
      }).join('');

      const xLabels = allPts.filter((_, i) => i === 0 || i === history.length - 1 || i === allPts.length - 1 || i % Math.max(1, Math.floor(allPts.length / 5)) === 0)
        .map((p) => {
          const idx = allPts.indexOf(p);
          return `<text x="${xAt(idx).toFixed(1)}" y="${H - 10}" fill="#94a3b8" font-size="10" text-anchor="middle">${p.date.slice(5)}</text>`;
        }).join('');

      cont.innerHTML = `
        <svg viewBox="0 0 ${W} ${H}" style="width: 100%; max-width: 100%; height: auto; display: block;">
          <line x1="${padL}" y1="${y100}" x2="${W - padR}" y2="${y100}" stroke="#ef4444" stroke-dasharray="4,4" stroke-width="1" />
          <text x="${padL - 6}" y="${parseFloat(y100) + 4}" fill="#ef4444" font-size="10" text-anchor="end">100% (${formatBytes(totalB)})</text>

          <line x1="${padL}" y1="${y80}" x2="${W - padR}" y2="${y80}" stroke="#f59e0b" stroke-dasharray="4,4" stroke-width="1" />
          <text x="${padL - 6}" y="${parseFloat(y80) + 4}" fill="#f59e0b" font-size="10" text-anchor="end">80% Warn</text>

          <line x1="${padL}" y1="${y50}" x2="${W - padR}" y2="${y50}" stroke="#334155" stroke-dasharray="2,4" stroke-width="1" />
          <text x="${padL - 6}" y="${parseFloat(y50) + 4}" fill="#64748b" font-size="10" text-anchor="end">50%</text>

          <line x1="${padL}" y1="${padT + plotH}" x2="${W - padR}" y2="${padT + plotH}" stroke="#334155" stroke-width="1" />

          ${actualPts.length >= 2 ? `<polyline fill="none" stroke="#38bdf8" stroke-width="2.5" points="${actualPts.join(' ')}" />` : ''}
          ${forecastPts.length >= 2 ? `<polyline fill="none" stroke="#a78bfa" stroke-width="2.5" stroke-dasharray="6,4" points="${forecastPts.join(' ')}" />` : ''}
          ${circles}
          ${xLabels}
        </svg>
        <div style="display:flex; gap:1.25rem; justify-content:center; margin-top:0.5rem; font-size:0.78rem; color:var(--text-muted);">
          <span><span style="color:#38bdf8;">━━</span> Historical Used</span>
          <span><span style="color:#a78bfa;">╍╍</span> 30-Day Linear Projection</span>
          <span><span style="color:#f59e0b;">╍╍</span> 80% Warning</span>
          <span><span style="color:#ef4444;">╍╍</span> 100% Capacity</span>
        </div>
      `;
    }

    // ── VIEW 6: Interactive AI Chat Logic ─────────────────────────────
    function askChip(prompt) {
      const inp = document.getElementById('chat-user-input');
      inp.value = prompt;
      sendChat(prompt);
    }

    function handleChatKey(e) {
      if (e.key === 'Enter') {
        sendChatFromInput();
      }
    }

    function sendChatFromInput() {
      const inp = document.getElementById('chat-user-input');
      const msg = inp.value.trim();
      if (!msg) return;
      inp.value = '';
      sendChat(msg);
    }

    async function sendChat(message) {
      const box = document.getElementById('chat-messages-box');
      const sendBtn = document.getElementById('chat-send-btn');

      // 1. Append user bubble
      const userBubble = document.createElement('div');
      userBubble.className = 'chat-msg chat-msg-user';
      userBubble.innerText = message;
      box.appendChild(userBubble);

      // 2. Append typing indicator
      const typingEl = document.createElement('div');
      typingEl.className = 'chat-msg chat-msg-ai';
      typingEl.id = 'chat-typing-indicator';
      typingEl.innerHTML = '<span class="typing-indicator"><span class="typing-dot"></span><span class="typing-dot"></span><span class="typing-dot"></span></span> Analyzing live backup metrics...';
      box.appendChild(typingEl);
      box.scrollTop = box.scrollHeight;

      sendBtn.disabled = true;

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: message })
        });
        const data = await resp.json();
        typingEl.remove();

        const aiBubble = document.createElement('div');
        aiBubble.className = 'chat-msg chat-msg-ai';
        let replyHtml = (data.reply || 'No response')
          .replace(/\n/g, '<br>')
          .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
          .replace(/`([^`]+)`/g, '<code style="background:#0b0f19; padding:2px 4px; border-radius:3px; color:#93c5fd;">$1</code>');
        aiBubble.innerHTML = replyHtml;
        box.appendChild(aiBubble);
      } catch (err) {
        typingEl.remove();
        const errBubble = document.createElement('div');
        errBubble.className = 'chat-msg chat-msg-ai';
        errBubble.style.borderColor = 'var(--fail)';
        errBubble.innerHTML = '❌ <strong>Error:</strong> ' + err.message;
        box.appendChild(errBubble);
      } finally {
        sendBtn.disabled = false;
        box.scrollTop = box.scrollHeight;
      }
    }

    function clearChat() {
      const box = document.getElementById('chat-messages-box');
      box.innerHTML = `
        <div class="chat-msg chat-msg-ai">
          👋 Chat cleared. Ask me any question about your database backups, storage capacity, or retention!
        </div>
      `;
    }

    async function triggerScan() {
      const btn = document.getElementById('btn-scan');
      btn.disabled = true;
      btn.innerText = '⏳ Scanning...';
      try {
        const resp = await fetch('/api/scan', { method: 'POST' });
        const res = await resp.json();
        document.getElementById('scan-status').innerText = res.message || 'Scan triggered';
        setTimeout(() => {
          loadData();
          loadAISummary();
          btn.disabled = false;
          btn.innerText = '⚡ Trigger Scan Now';
        }, 1500);
      } catch (e) {
        alert('Failed to trigger scan: ' + e.message);
        btn.disabled = false;
        btn.innerText = '⚡ Trigger Scan Now';
      }
    }

    // Initial load
    loadData();
    loadAISummary();
    setInterval(loadData, 30000);
  </script>
</body>
</html>
"""


class BackupMonitorRequestHandler(BaseHTTPRequestHandler):
    """Handles HTTP requests for Web UI, Metrics, and REST API."""

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress noisy request logs, keep warnings and errors."""
        if args and str(args[1]) in ("200", "304") and not logger.isEnabledFor(logging.DEBUG):
            return
        logger.debug("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def _check_basic_auth(self, path: str) -> bool:
        """Enforce HTTP Basic Authentication when configured (no database required)."""
        if path in ("/healthz", "/live", "/ready"):
            return True
        if _auth_config is None or not getattr(_auth_config, "enabled", False):
            return True

        expected_user = getattr(_auth_config, "username", "admin") or "admin"
        expected_pass = getattr(_auth_config, "password", "") or ""
        if not expected_pass:
            return False

        auth_header = self.headers.get("Authorization", "")
        if auth_header.lower().startswith("basic "):
            try:
                decoded = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
                if ":" in decoded:
                    user, pwd = decoded.split(":", 1)
                    if hmac.compare_digest(user, expected_user) and hmac.compare_digest(pwd, expected_pass):
                        return True
            except Exception:
                pass

        self.send_response(HTTPStatus.UNAUTHORIZED)
        self.send_header("WWW-Authenticate", 'Basic realm="Backwatch Admin"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Unauthorized: Valid Backwatch admin credentials required.")
        return False

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if not self._check_basic_auth(path):
            return

        # 1. Web UI Dashboard
        if path in ("/", "/dashboard", "/ui"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(DASHBOARD_HTML.encode("utf-8"))
            return

        # 2. Prometheus Metrics
        if path == "/metrics":
            try:
                metrics_data = generate_latest()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", CONTENT_TYPE_LATEST)
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(metrics_data)
            except Exception as e:
                logger.error("Failed to generate Prometheus metrics: %s", e)
                self.send_response(HTTPStatus.INTERNAL_SERVER_ERROR)
                self.end_headers()
            return

        # 3. Latest JSON Report
        if path in ("/api/report", "/api/latest-report"):
            report_file = Path(_state_dir) / "latest-report.json"
            if report_file.exists():
                try:
                    data = report_file.read_bytes()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/json; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(data)
                    return
                except Exception as e:
                    logger.error("Failed reading report file: %s", e)

            # Fallback if report file doesn't exist yet
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "summary": {"total_databases": 0, "healthy": 0, "warning": 0, "failed": 0},
                "groups": [],
                "databases": [],
                "servers": [],
                "retention_candidates": [],
                "restore_results": [],
                "df_mounts": [],
                "storage_projection": {"source": "none", "total_bytes": 0, "used_bytes": 0, "free_bytes": 0, "daily_growth_bytes": 0, "days_until_full": None, "status": "stable", "history": [], "forecast": []},
                "folder_tree": [],
                "system_storage": {"total_bytes": 0, "used_bytes": 0, "free_bytes": 0, "percent_used": 0.0, "mount_point": "/backups"},
                "system_files": {"total_count": 0, "total_bytes": 0, "scripts_count": 0, "logs_count": 0, "sample_files": []},
                "message": "Initial scan in progress...",
            }).encode("utf-8"))
            return

        # 4. Quick Summary API
        if path == "/api/summary":
            report_file = Path(_state_dir) / "latest-report.json"
            summary = {"total_groups": 0, "healthy": 0, "warning": 0, "failed": 0}
            if report_file.exists():
                try:
                    data = json.loads(report_file.read_text(encoding="utf-8"))
                    summary = data.get("summary", summary)
                except Exception:
                    pass
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps(summary).encode("utf-8"))
            return

        # 5. AI Summary API
        if path == "/api/ai-summary":
            ai_file = Path(_state_dir) / "ai-summary.txt"
            content = ""
            if ai_file.exists():
                try:
                    content = ai_file.read_text(encoding="utf-8")
                except Exception:
                    pass
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
            return

        # 6. Healthcheck
        if path in ("/healthz", "/live", "/ready"):
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        # Not Found
        self.send_response(HTTPStatus.NOT_FOUND)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Not Found")

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if not self._check_basic_auth(path):
            return

        # 1. Trigger immediate scan
        if path in ("/api/scan", "/scan"):
            global _is_scanning
            if _trigger_scan_fn is None:
                self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Scanner not initialized"}')
                return

            if _is_scanning:
                self.send_response(HTTPStatus.CONFLICT)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"message": "Scan already in progress"}')
                return

            def _async_scan():
                global _is_scanning
                with _scan_lock:
                    if _is_scanning:
                        return
                    _is_scanning = True
                try:
                    _trigger_scan_fn()
                finally:
                    with _scan_lock:
                        _is_scanning = False

            thread = threading.Thread(target=_async_scan, daemon=True, name="ManualScanThread")
            thread.start()

            self.send_response(HTTPStatus.ACCEPTED)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"message": "Scan triggered successfully"}')
            return

        # 2. Interactive AI Chat Query
        if path in ("/api/chat", "/chat"):
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
            try:
                data = json.loads(body)
                user_msg = data.get("message", "").strip()
            except Exception:
                user_msg = ""

            if not user_msg:
                self.send_response(HTTPStatus.BAD_REQUEST)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error": "Message is required"}')
                return

            report_file = Path(_state_dir) / "latest-report.json"
            report_data = {}
            if report_file.exists():
                try:
                    report_data = json.loads(report_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            ai_cfg = _ai_config
            if ai_cfg is None:
                try:
                    from app.config import load_config
                    ai_cfg = load_config().ai
                except Exception:
                    from app.config import AIConfig
                    ai_cfg = AIConfig()

            from app.ai.summarizer import answer_backup_query
            reply = answer_backup_query(user_msg, report_data, ai_cfg)

            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"reply": reply, "status": "ok"}).encode("utf-8"))
            return

        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()


def start_integrated_server(
    host: str = "0.0.0.0",
    port: int = 9108,
    state_dir: str = "/state",
    trigger_scan_fn: Optional[Callable[[], None]] = None,
    ai_config: Optional[Any] = None,
    auth_config: Optional[Any] = None,
) -> ThreadingHTTPServer:
    """Start the integrated Web UI, REST API, and Prometheus metrics server."""
    global _state_dir, _trigger_scan_fn, _ai_config, _auth_config
    _state_dir = state_dir
    _trigger_scan_fn = trigger_scan_fn
    _ai_config = ai_config
    _auth_config = auth_config

    server = ThreadingHTTPServer((host, port), BackupMonitorRequestHandler)
    server_thread = threading.Thread(
        target=server.serve_forever,
        daemon=True,
        name="IntegratedWebServer",
    )
    server_thread.start()
    logger.info("Integrated Web UI & Prometheus metrics server listening at http://%s:%d/", host, port)
    return server
