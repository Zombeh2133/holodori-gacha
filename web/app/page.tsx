"use client";

import { useEffect, useMemo, useState } from "react";
import { characterNameFromCardId } from "./characterMap";

const API = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

type Account = {
  id: string;
  region: string;
  game_account_id: string;
  nickname: string | null;
  pull_count: number;
};

type Me = {
  authenticated: boolean;
  user_id: string;
  discord_user_id: string;
  discord_username: string | null;
  game_accounts: Account[];
};

type Pull = {
  id: string;
  item_id: string;
  item_name: string;
  rarity: number;
  pulled_at: string | null;
  pull_order: number;
  occurrence_index: number;
  is_featured: boolean | null;
  banner: {
    id: string;
    game_banner_id: string | null;
    name: string;
    banner_type: string;
  };
};

type Banner = {
  id: string;
  game_banner_id: string;
  name: string;
  banner_type: string;
  total_pulls: number;
  five_star_count: number;
  four_star_count: number;
  current_five_star_pity: number;
  last_five_star_pity: number | null;
};

type Dashboard = {
  account: {
    id: string;
    region: string;
    game_account_id: string;
    nickname: string | null;
  };
  selected_banner: null | {
    id: string;
    game_banner_id: string;
    name: string;
    banner_type: string;
  };
  stats: {
    total_pulls: number;
    five_star_pulls: number;
    four_star_pulls: number;
    three_star_pulls: number;
    average_five_star_pity: number | null;
    current_five_star_pity: number;
    banner_count: number;
  };
  banners: Banner[];
  recent_five_star_pulls: Pull[];
  recent_pulls: Pull[];
  latest_import: null | {
    source: string;
    importer_version: string | null;
    total_pulls: number;
    new_pulls: number;
    duplicate_pulls: number;
    imported_at: string;
  };
};

type HistoryResult = {
  page: number;
  page_size: number;
  total: number;
  total_pages: number;
  rarities: number[];
  banner_id: string | null;
  rows: Pull[];
};


type SiteImportFile = {
  region: "US" | "ASIA" | "JP";
  game_account_id: string;
  account_nickname?: string | null;
  importer_version?: string | null;
  pulls: Array<{
    gacha_name: string;
    game_banner_id?: string | null;
    banner_type?: string | null;
    card_id: string;
    item_name?: string | null;
    rarity?: number | null;
    draw_time?: number | null;
    pulled_at?: string | null;
    occurrence_index?: number | null;
    is_featured?: boolean | null;
    raw_source_id?: string | null;
  }>;
};

type SiteImportResult = {
  import_id: string;
  account_id: string;
  game_account_id: string;
  region: string;
  total_pulls: number;
  new_pulls: number;
  duplicate_pulls: number;
};


type LocalImportToken = {
  token: string;
  expires_at: string;
  expires_in_seconds: number;
};

type ViewName = "dashboard" | "history" | "import";


function buildPublicPowerShell(
  token: string,
) {
  const psQuote = (value: string) => value.replace(/'/g, "''");
  const apiBase = psQuote(API);
  const importToken = psQuote(token);

  return `$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$ApiBase = '${apiBase}'
$ImportToken = '${importToken}'

Write-Host ''
Write-Host 'Holodori Gacha Importer' -ForegroundColor Cyan
Write-Host '=======================' -ForegroundColor Cyan
Write-Host ''

$credential = $null
$body = $null

try {
    Write-Host '[1/3] Finding your hololive Dreams account...' -ForegroundColor Cyan

    $root = Join-Path $env:USERPROFILE 'AppData\\LocalLow\\QualiArts, Inc_\\hololive-Dreams'

    if (-not (Test-Path -LiteralPath $root)) {
        throw 'hololive Dreams was not found. Launch the game and log in first.'
    }

    $dcFile = Get-ChildItem -LiteralPath $root -File -ErrorAction Stop |
        Where-Object { $_.Name -like 'DC*' } |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1

    if ($null -eq $dcFile) {
        throw 'No logged-in hololive Dreams account was found. Launch the game and log in first.'
    }

    $credential = [System.IO.File]::ReadAllText($dcFile.FullName)
    $credential = $credential.Trim([char]0xFEFF).Trim()

    if ([string]::IsNullOrWhiteSpace($credential)) {
        throw 'The hololive Dreams account file was empty.'
    }

    Write-Host '      Account found.' -ForegroundColor Green
    Write-Host ''
    Write-Host '[2/3] Fetching your Gacha Log...' -ForegroundColor Cyan

    $body = @{
        credential = $credential
    } | ConvertTo-Json -Compress

    $result = Invoke-RestMethod `
        -Uri ($ApiBase.TrimEnd('/') + '/api/import/public-game') `
        -Method Post `
        -Headers @{
            Authorization = ('Bearer ' + $ImportToken)
        } `
        -ContentType 'application/json' `
        -Body $body `
        -TimeoutSec 90

    $credential = $null
    $body = $null

    Write-Host '      Gacha Log received.' -ForegroundColor Green
    Write-Host ''
    Write-Host '[3/3] Import complete!' -ForegroundColor Green
    Write-Host ''
    Write-Host ('Region:     ' + [string]$result.region)
    Write-Host ('Submitted:  ' + [string]$result.total_pulls)
    Write-Host ('New:        ' + [string]$result.new_pulls) -ForegroundColor Green
    Write-Host ('Duplicates: ' + [string]$result.duplicate_pulls) -ForegroundColor Yellow
    Write-Host ''
    Write-Host 'Refresh Holodori Gacha to see your updated history.' -ForegroundColor Cyan
}
catch {
    Write-Host ''
    Write-Host 'Import failed:' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red

    if ($_.Exception.Message -match '401|token|expired') {
        Write-Host ''
        Write-Host 'Generate a fresh PowerShell script from the website and try again.' -ForegroundColor Yellow
    }
}
finally {
    $credential = $null
    $body = $null
    Remove-Variable ImportToken -ErrorAction SilentlyContinue
}

Write-Host ''
`;
}

function initials(value: string) {
  const chunks = value.trim().split(/\s+/).filter(Boolean);
  return (chunks.slice(0, 2).map((part) => part[0]?.toUpperCase()).join("") || "?").slice(0, 2);
}

function prettyDate(value: string | null) {
  if (!value) return "Unknown time";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function bannerTypeLabel(value: string | null | undefined) {
  switch ((value || "").trim().toLowerCase()) {
    case "select":
      return "Member Select";
    case "pickup":
      return "Standard Pickup";
    case "standard":
      return "Standard";
    case "guaranteed":
      return "Guaranteed";
    case "collaboration":
      return "Collaboration";
    case "beginner":
      return "Beginner";
    default:
      return "Gacha";
  }
}
function rarityQuery(filters: Set<number>) {
  return Array.from(filters).sort((a, b) => a - b).join(",");
}


function ArtworkImage({
  kind,
  id,
  recordId,
  alt,
  className = "",
}: {
  kind: "card" | "banner";
  id: string | null | undefined;
  recordId?: string | null;
  alt: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);

  const usable =
    !failed &&
    (
      kind === "card"
        ? !!id && id.startsWith("card-")
        : !!recordId || (!!id && id.startsWith("gacha-"))
    );

  if (!usable) return null;

  const src =
    kind === "banner" && recordId
      ? `${API}/api/assets/banner-record/${encodeURIComponent(recordId)}?v=3`
      : `${API}/api/assets/${kind}/${encodeURIComponent(id || "")}`;

  return (
    <img
      className={className}
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

export default function Home() {
  const [me, setMe] = useState<Me | null>(null);
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [history, setHistory] = useState<HistoryResult | null>(null);
  const [dashboardHistory, setDashboardHistory] = useState<HistoryResult | null>(null);
  const [selectedAccount, setSelectedAccount] = useState<string>("");
  const [selectedBanner, setSelectedBanner] = useState<string | null>(null);
  const [activeView, setActiveView] = useState<ViewName>("dashboard");
  const [historyPage, setHistoryPage] = useState(1);
  const [dashboardHistoryPage, setDashboardHistoryPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [dashboardLoading, setDashboardLoading] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [dashboardHistoryLoading, setDashboardHistoryLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [rarityFilters, setRarityFilters] = useState<Set<number>>(() => new Set([4, 5]));
  const [importPreview, setImportPreview] = useState<SiteImportFile | null>(null);
  const [importFilename, setImportFilename] = useState("");
  const [importError, setImportError] = useState("");
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<SiteImportResult | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function loadMe() {
      try {
        const response = await fetch(`${API}/api/me`, {
          credentials: "include",
        });

        if (response.status === 401) {
          if (!cancelled) setMe(null);
          return;
        }

        if (!response.ok) {
          throw new Error(`Account request failed (${response.status})`);
        }

        const payload: Me = await response.json();

        if (!cancelled) {
          setMe(payload);
          setSelectedAccount((current) => current || payload.game_accounts[0]?.id || "");
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load account");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }

    loadMe();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    setSelectedBanner(null);
    setHistoryPage(1);
    setDashboardHistoryPage(1);
    setHistory(null);
    setDashboardHistory(null);
  }, [selectedAccount]);

  useEffect(() => {
    if (!selectedAccount) {
      setDashboard(null);
      return;
    }

    let cancelled = false;

    async function loadDashboard() {
      try {
        setDashboardLoading(true);
        setError("");

        const params = new URLSearchParams();
        if (selectedBanner) params.set("banner_id", selectedBanner);

        const suffix = params.size ? `?${params.toString()}` : "";
        const response = await fetch(
          `${API}/api/accounts/${selectedAccount}/dashboard${suffix}`,
          { credentials: "include" },
        );

        if (!response.ok) {
          throw new Error(`Dashboard request failed (${response.status})`);
        }

        const payload: Dashboard = await response.json();

        if (!cancelled) setDashboard(payload);
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load dashboard");
        }
      } finally {
        if (!cancelled) setDashboardLoading(false);
      }
    }

    loadDashboard();
    return () => {
      cancelled = true;
    };
  }, [selectedAccount, selectedBanner]);

  useEffect(() => {
    if (activeView !== "dashboard" || !selectedAccount) return;

    let cancelled = false;

    async function loadDashboardHistory() {
      try {
        setDashboardHistoryLoading(true);
        setError("");

        const params = new URLSearchParams({
          page: String(dashboardHistoryPage),
          page_size: "30",
          rarities: rarityQuery(rarityFilters),
        });

        if (selectedBanner) {
          params.set("banner_id", selectedBanner);
        }

        const response = await fetch(
          `${API}/api/accounts/${selectedAccount}/history?${params.toString()}`,
          { credentials: "include" },
        );

        if (!response.ok) {
          throw new Error(`Pull history request failed (${response.status})`);
        }

        const payload: HistoryResult = await response.json();

        if (!cancelled) {
          setDashboardHistory(payload);
          if (payload.page !== dashboardHistoryPage) {
            setDashboardHistoryPage(payload.page);
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load pull history");
        }
      } finally {
        if (!cancelled) setDashboardHistoryLoading(false);
      }
    }

    loadDashboardHistory();

    return () => {
      cancelled = true;
    };
  }, [
    activeView,
    selectedAccount,
    selectedBanner,
    dashboardHistoryPage,
    rarityFilters,
  ]);

  useEffect(() => {
    if (activeView !== "history" || !selectedAccount) return;

    let cancelled = false;

    async function loadHistory() {
      try {
        setHistoryLoading(true);
        setError("");

        const params = new URLSearchParams({
          page: String(historyPage),
          page_size: "30",
          rarities: rarityQuery(rarityFilters),
        });

        if (selectedBanner) {
          params.set("banner_id", selectedBanner);
        }

        const response = await fetch(
          `${API}/api/accounts/${selectedAccount}/history?${params.toString()}`,
          { credentials: "include" },
        );

        if (!response.ok) {
          throw new Error(`History request failed (${response.status})`);
        }

        const payload: HistoryResult = await response.json();

        if (!cancelled) {
          setHistory(payload);
          if (payload.page !== historyPage) setHistoryPage(payload.page);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load history");
        }
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    }

    loadHistory();

    return () => {
      cancelled = true;
    };
  }, [activeView, selectedAccount, selectedBanner, historyPage, rarityFilters]);

  const selected = useMemo(
    () => me?.game_accounts.find((account) => account.id === selectedAccount) || null,
    [me, selectedAccount],
  );

  const selectedBannerData = useMemo(
    () => dashboard?.banners.find((banner) => banner.id === selectedBanner) || null,
    [dashboard, selectedBanner],
  );

  async function refreshMe(preferredAccountId?: string) {
    const response = await fetch(`${API}/api/me`, {
      credentials: "include",
    });

    if (!response.ok) {
      throw new Error(`Account refresh failed (${response.status})`);
    }

    const payload: Me = await response.json();
    setMe(payload);

    if (preferredAccountId) {
      setSelectedAccount(preferredAccountId);
    } else if (!selectedAccount && payload.game_accounts[0]?.id) {
      setSelectedAccount(payload.game_accounts[0].id);
    }

    return payload;
  }

  async function handleImportFile(file: File | null) {
    setImportPreview(null);
    setImportResult(null);
    setImportError("");
    setImportFilename(file?.name || "");

    if (!file) return;

    try {
      const raw = await file.text();
      const parsed = JSON.parse(raw) as SiteImportFile;

      if (!parsed || typeof parsed !== "object") {
        throw new Error("The selected file does not contain an import object.");
      }

      if (!["US", "ASIA", "JP"].includes(parsed.region)) {
        throw new Error("region must be US, ASIA, or JP.");
      }

      if (!String(parsed.game_account_id || "").trim()) {
        throw new Error("game_account_id is missing.");
      }

      if (!Array.isArray(parsed.pulls) || parsed.pulls.length === 0) {
        throw new Error("No pulls were found in the file.");
      }

      for (const [index, pull] of parsed.pulls.entries()) {
        if (!String(pull.gacha_name || "").trim()) {
          throw new Error(`Pull ${index + 1} is missing gacha_name.`);
        }
        if (!String(pull.card_id || "").trim()) {
          throw new Error(`Pull ${index + 1} is missing card_id.`);
        }
        if (!pull.pulled_at && !pull.draw_time) {
          throw new Error(`Pull ${index + 1} needs pulled_at or draw_time.`);
        }
      }

      setImportPreview(parsed);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Could not read import file.");
    }
  }

  async function submitImport() {
    if (!importPreview) return;

    setImporting(true);
    setImportError("");
    setImportResult(null);

    try {
      const response = await fetch(`${API}/api/import/pulls`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(importPreview),
      });

      const payload = await response.json();

      if (!response.ok) {
        const detail =
          typeof payload?.detail === "string"
            ? payload.detail
            : JSON.stringify(payload?.detail || payload);
        throw new Error(detail || `Import failed (${response.status})`);
      }

      const result = payload as SiteImportResult;
      setImportResult(result);
      await refreshMe(result.account_id);
      setSelectedBanner(null);
      setDashboard(null);
      setHistory(null);
      setDashboardHistory(null);
      setDashboardHistoryPage(1);
    } catch (err) {
      setImportError(err instanceof Error ? err.message : "Import failed.");
    } finally {
      setImporting(false);
    }
  }

  function toggleRarity(rarity: number) {
    setRarityFilters((current) => {
      const next = new Set(current);
      if (next.has(rarity)) next.delete(rarity);
      else next.add(rarity);
      return next;
    });

    setHistoryPage(1);
    setDashboardHistoryPage(1);
  }

  function chooseBanner(id: string | null) {
    setSelectedBanner(id);
    setHistoryPage(1);
    setDashboardHistoryPage(1);
    setDashboardHistory(null);
  }

  if (loading) {
    return (
      <main className="center-screen">
        <div className="loader" />
        Loading Holodori Gacha…
      </main>
    );
  }

  if (!me) {
    return (
      <main className="login-shell">
        <div className="login-card">
          <div className="brand-mark">HG</div>
          <p className="eyebrow">HOLODORI GACHA</p>
          <h1>Your pulls, pity, and history in one place.</h1>
          <p className="muted">
            Sign in with Discord to claim gacha data already uploaded for your Discord ID.
            We request only Discord&apos;s identify scope.
          </p>
          {error && <div className="error-box">{error}</div>}
          <a className="discord-button" href={`${API}/auth/discord/login`}>
            Continue with Discord
          </a>
          <p className="privacy-note">Discord OAuth tokens are not stored by Holodori Gacha.</p>
        </div>
      </main>
    );
  }

  return (
    <main className="app-shell">
      <header className="topbar">
        <div className="brand-row">
          <div className="brand-mark small">HG</div>
          <div>
            <div className="brand-title">Holodori Gacha</div>
            <div className="brand-subtitle">Pull Tracker</div>
          </div>
        </div>

        <nav className="topnav">
          <button
            className={activeView === "dashboard" ? "nav-active" : ""}
            onClick={() => setActiveView("dashboard")}
          >
            Dashboard
          </button>
          <button
            className={activeView === "history" ? "nav-active" : ""}
            onClick={() => setActiveView("history")}
          >
            History
          </button>
          <button
            className={activeView === "import" ? "nav-active" : ""}
            onClick={() => setActiveView("import")}
          >
            Import
          </button>
        </nav>

        <div className="user-area">
          <div className="user-copy">
            <strong>{me.discord_username || "Discord User"}</strong>
            <span className="linked-status">
              <i className="linked-dot" />
              Discord linked
            </span>
          </div>
          <a className="ghost-button" href={`${API}/auth/logout`}>
            Log out
          </a>
        </div>
      </header>

      <div className="workspace">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <span>Game Accounts</span>
            <span className="count-badge">{me.game_accounts.length}</span>
          </div>

          {me.game_accounts.length === 0 && (
            <div className="empty-sidebar">No game accounts have been imported yet.</div>
          )}

          {me.game_accounts.map((account) => (
            <button
              className={`account-card ${account.id === selectedAccount ? "selected" : ""}`}
              key={account.id}
              onClick={() => setSelectedAccount(account.id)}
            >
              <div className="region-pill">{account.region}</div>
              <div className="account-copy">
                <strong>{account.nickname || account.game_account_id}</strong>
                <span>{account.game_account_id}</span>
              </div>
              <div className="pull-total">
                <strong>{account.pull_count}</strong>
                <span>pulls</span>
              </div>
            </button>
          ))}

          <div className="sidebar-section-divider" />

          <div className="sidebar-heading banner-heading">
            <span>Banners</span>
            <span className="count-badge">{dashboard?.banners.length || 0}</span>
          </div>

          {!!dashboard?.banners.length && (
            <button
              type="button"
              className={`sidebar-banner-card sidebar-banner-button all-banners-card ${selectedBanner === null ? "selected" : ""}`}
              onClick={() => chooseBanner(null)}
            >
              <div className="sidebar-banner-art all-banner-art" aria-hidden="true">
                <span>ALL</span>
              </div>
              <div className="sidebar-banner-copy">
                <strong>All Banners</strong>
                <span>Lifetime statistics</span>
              </div>
              <div className="sidebar-banner-pity">
                <strong>{selected?.pull_count ?? 0}</strong>
                <span>pulls</span>
              </div>
            </button>
          )}

          {dashboard?.banners.length ? (
            <div className="sidebar-banner-list">
              {dashboard.banners.map((banner, index) => (
                <button
                  type="button"
                  className={`sidebar-banner-card sidebar-banner-button ${banner.id === selectedBanner ? "selected" : ""}`}
                  key={banner.id}
                  onClick={() => chooseBanner(banner.id)}
                >
                  <div className={`sidebar-banner-art banner-art-${index % 4}`}>
                    <ArtworkImage
                      kind="banner"
                      id={banner.game_banner_id}
                      recordId={banner.id}
                      alt=""
                      className="artwork-fill"
                    />
                    <span className="artwork-fallback">{initials(banner.name)}</span>
                  </div>
                  <div className="sidebar-banner-copy">
                    <strong>{banner.name}</strong>
                    <span>{bannerTypeLabel(banner.banner_type)}</span>
                  </div>
                  <div className="sidebar-banner-pity">
                    <strong>{banner.total_pulls}</strong>
                    <span>pulls</span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <div className="empty-sidebar">No banners imported yet.</div>
          )}
        </aside>

        <section className="content">
          <div className="hero-row">
            <div>
              <p className="eyebrow">
                {selectedBannerData ? bannerTypeLabel(selectedBannerData.banner_type).toUpperCase() : `${selected?.region || "—"} ACCOUNT`}
              </p>
              <h1>
                {selectedBannerData?.name || selected?.nickname || "Gacha Dashboard"}
              </h1>
              <p className="muted">
                {selectedBannerData
                  ? `${selected?.nickname || selected?.game_account_id || "Account"} · ${selectedBannerData.total_pulls} pulls on this banner`
                  : "Track lifetime pulls, pity, banners, and recent high-rarity results."}
              </p>
            </div>

            {dashboard?.latest_import && (
              <div className="sync-card">
                <span>Last synchronized</span>
                <strong>{prettyDate(dashboard.latest_import.imported_at)}</strong>
                <small>
                  {dashboard.latest_import.new_pulls} new · {dashboard.latest_import.duplicate_pulls} duplicates ignored
                </small>
              </div>
            )}
          </div>

          {error && <div className="error-box">{error}</div>}

          {activeView === "dashboard" ? (
            <DashboardView
              dashboard={dashboard}
              loading={dashboardLoading}
              selectedAccount={selectedAccount}
              rarityFilters={rarityFilters}
              dashboardHistory={dashboardHistory}
              dashboardHistoryLoading={dashboardHistoryLoading}
              dashboardHistoryPage={dashboardHistoryPage}
              setDashboardHistoryPage={setDashboardHistoryPage}
              toggleRarity={toggleRarity}
              chooseBanner={chooseBanner}
              selectedBanner={selectedBanner}
            />
          ) : activeView === "history" ? (
            <HistoryView
              history={history}
              loading={historyLoading}
              selectedAccount={selectedAccount}
              rarityFilters={rarityFilters}
              toggleRarity={toggleRarity}
              page={historyPage}
              setPage={setHistoryPage}
              selectedBannerData={selectedBannerData}
            />
          ) : (
            <ImportView
              preview={importPreview}
              filename={importFilename}
              error={importError}
              importing={importing}
              result={importResult}
              onFile={handleImportFile}
              onImport={submitImport}
              onViewDashboard={() => setActiveView("dashboard")}
            />
          )}
        </section>
      </div>
    </main>
  );
}

function DashboardView({
  dashboard,
  loading,
  selectedAccount,
  rarityFilters,
  dashboardHistory,
  dashboardHistoryLoading,
  dashboardHistoryPage,
  setDashboardHistoryPage,
  toggleRarity,
  chooseBanner,
  selectedBanner,
}: {
  dashboard: Dashboard | null;
  loading: boolean;
  selectedAccount: string;
  rarityFilters: Set<number>;
  dashboardHistory: HistoryResult | null;
  dashboardHistoryLoading: boolean;
  dashboardHistoryPage: number;
  setDashboardHistoryPage: (page: number) => void;
  toggleRarity: (rarity: number) => void;
  chooseBanner: (id: string | null) => void;
  selectedBanner: string | null;
}) {
  if (loading && !dashboard) {
    return (
      <div className="panel loading-panel">
        <div className="loader" />
        Loading account statistics…
      </div>
    );
  }

  if (!dashboard && selectedAccount) {
    return (
      <div className="panel loading-panel">
        <div className="loader" />
        Loading account statistics…
      </div>
    );
  }

  if (!dashboard) {
    return (
      <div className="panel">
        <EmptyState text="Import a game account to begin tracking pulls." />
      </div>
    );
  }

  const SPARK_THRESHOLD = 200;

  const pullsNeededForNextSpark = (pulls: number) => {
    const progress = Math.max(0, pulls) % SPARK_THRESHOLD;
    return progress === 0 ? SPARK_THRESHOLD : SPARK_THRESHOLD - progress;
  };

  const selectedSparkBanner = selectedBanner
    ? dashboard.banners.find((banner) => banner.id === selectedBanner) || null
    : null;

  const pullsNeededTillSpark = selectedSparkBanner
    ? pullsNeededForNextSpark(selectedSparkBanner.total_pulls)
    : dashboard.banners.length
      ? Math.min(
          ...dashboard.banners.map((banner) =>
            pullsNeededForNextSpark(banner.total_pulls),
          ),
        )
      : SPARK_THRESHOLD;

  const sparked = selectedSparkBanner
    ? Math.floor(selectedSparkBanner.total_pulls / SPARK_THRESHOLD)
    : dashboard.banners.reduce(
        (total, banner) => total + Math.floor(banner.total_pulls / SPARK_THRESHOLD),
        0,
      );

  return (
    <>
      <section className="stats-grid">
        <StatCard
          label="Total Pulls"
          value={dashboard.stats.total_pulls}
          hint={`${dashboard.stats.banner_count} banner${dashboard.stats.banner_count === 1 ? "" : "s"}`}
        />
        <StatCard
          label="5★ Pulls"
          value={dashboard.stats.five_star_pulls}
          accent="gold"
          hint={selectedBanner ? "Selected banner" : "Lifetime"}
        />
        <StatCard
          label="4★ Pulls"
          value={dashboard.stats.four_star_pulls}
          accent="purple"
          hint={selectedBanner ? "Selected banner" : "Lifetime"}
        />
        <StatCard
          label="Pulls Needed Till Spark"
          value={pullsNeededTillSpark}
          accent="blue"
          hint={selectedBanner ? "Selected banner · 200 pulls per spark" : "Closest banner · 200 pulls per spark"}
        />
        <StatCard
          label="Sparked"
          value={sparked}
          accent="gold"
          hint={selectedBanner ? "Selected banner" : "Completed sparks across banners"}
        />
      </section>

      <section className="two-column">
        <div className="panel recent-five-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">RECENT LUCK</p>
              <h2>Recent 5★ Pulls</h2>
            </div>
            <span className="soft-badge">{dashboard.recent_five_star_pulls.length} shown</span>
          </div>

          {dashboard.recent_five_star_pulls.length ? (
            <div className="five-star-grid scrollable-five-star-grid">
              {dashboard.recent_five_star_pulls.map((pull) => (
                <div className="five-star-card" key={pull.id}>
                  <div className="item-avatar five artwork-avatar">
                    <ArtworkImage
                      kind="card"
                      id={pull.item_id}
                      alt={pull.item_name}
                      className="artwork-fill"
                    />
                    <span className="artwork-fallback">{initials(pull.item_name)}</span>
                  </div>
                  <strong>{pull.item_name}</strong>
                  <span>{pull.banner.name}</span>
                  <div className="rarity-line">★★★★★</div>
                </div>
              ))}
            </div>
          ) : (
            <EmptyState text="No 5★ pulls have been imported for this selection yet." />
          )}
        </div>

        <div className="panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">BANNER STATUS</p>
              <h2>Pulls to Spark</h2>
            </div>
          </div>

          {dashboard.banners.length ? (
            <div className="banner-list scrollable-banner-list">
              {dashboard.banners.map((banner) => (
                <button
                  type="button"
                  className={`banner-row banner-row-button ${banner.id === selectedBanner ? "selected" : ""}`}
                  key={banner.id}
                  onClick={() => chooseBanner(banner.id)}
                >
                  <div className="banner-icon artwork-avatar">
                    <ArtworkImage
                      kind="banner"
                      id={banner.game_banner_id}
                      recordId={banner.id}
                      alt=""
                      className="artwork-fill"
                    />
                    <span className="artwork-fallback">{initials(banner.name)}</span>
                  </div>
                  <div className="banner-copy">
                    <strong>{banner.name}</strong>
                    <span>{bannerTypeLabel(banner.banner_type)} · {banner.total_pulls} pulls</span>
                  </div>
                  <div className="pity-number">
                    <strong>{pullsNeededForNextSpark(banner.total_pulls)}</strong>
                    <span>to spark</span>
                  </div>
                </button>
              ))}
            </div>
          ) : (
            <EmptyState text="No banner data yet." />
          )}
        </div>
      </section>

      <section className="panel history-panel">
        <div className="panel-title-row history-title-row">
          <div>
            <p className="eyebrow">LATEST IMPORTED RESULTS</p>
            <h2>Recent Pull History</h2>
          </div>
          {dashboardHistory && (
            <span className="soft-badge">
              {dashboardHistory.rows.length} shown · {dashboardHistory.total} matching
            </span>
          )}
        </div>

        {dashboardHistoryLoading && !dashboardHistory ? (
          <div className="dashboard-history-loading">
            <div className="loader" />
            Loading pull history…
          </div>
        ) : dashboardHistory ? (
          <>
            <PullTable
              rows={dashboardHistory.rows}
              rarityFilters={rarityFilters}
              toggleRarity={toggleRarity}
            />

            {dashboardHistory.total_pages > 1 && (
              <div className="pagination-row compact-pagination">
                <button
                  type="button"
                  className="page-button"
                  disabled={dashboardHistory.page <= 1 || dashboardHistoryLoading}
                  onClick={() =>
                    setDashboardHistoryPage(Math.max(1, dashboardHistoryPage - 1))
                  }
                >
                  ← Previous
                </button>

                <span>
                  Page <strong>{dashboardHistory.page}</strong> of{" "}
                  <strong>{dashboardHistory.total_pages}</strong>
                </span>

                <button
                  type="button"
                  className="page-button"
                  disabled={
                    dashboardHistory.page >= dashboardHistory.total_pages ||
                    dashboardHistoryLoading
                  }
                  onClick={() =>
                    setDashboardHistoryPage(
                      Math.min(
                        dashboardHistory.total_pages,
                        dashboardHistoryPage + 1,
                      ),
                    )
                  }
                >
                  Next →
                </button>
              </div>
            )}
          </>
        ) : (
          <EmptyState text="No pulls have been imported yet." />
        )}
      </section>
    </>
  );
}

function HistoryView({
  history,
  loading,
  selectedAccount,
  rarityFilters,
  toggleRarity,
  page,
  setPage,
  selectedBannerData,
}: {
  history: HistoryResult | null;
  loading: boolean;
  selectedAccount: string;
  rarityFilters: Set<number>;
  toggleRarity: (rarity: number) => void;
  page: number;
  setPage: (page: number) => void;
  selectedBannerData: Banner | null;
}) {
  if (!selectedAccount) {
    return (
      <div className="panel">
        <EmptyState text="Import a game account to view pull history." />
      </div>
    );
  }

  return (
    <section className="panel history-panel full-history-panel">
      <div className="panel-title-row history-title-row full-history-title">
        <div>
          <p className="eyebrow">FULL HISTORY</p>
          <h2>{selectedBannerData ? selectedBannerData.name : "All Pulls"}</h2>
          <p className="history-subtitle">
            30 results per page · 3★ results are hidden by default.
          </p>
        </div>

        {history && (
          <div className="history-summary">
            <strong>{history.total}</strong>
            <span>matching pulls</span>
          </div>
        )}
      </div>

      {loading && !history ? (
        <div className="history-loading">
          <div className="loader" />
          Loading pull history…
        </div>
      ) : history ? (
        <>
          <PullTable
            rows={history.rows}
            rarityFilters={rarityFilters}
            toggleRarity={toggleRarity}
          />

          <div className="pagination-row">
            <button
              type="button"
              className="page-button"
              disabled={history.page <= 1 || loading}
              onClick={() => setPage(Math.max(1, page - 1))}
            >
              ← Previous
            </button>

            <span>
              Page <strong>{history.page}</strong> of <strong>{history.total_pages}</strong>
            </span>

            <button
              type="button"
              className="page-button"
              disabled={history.page >= history.total_pages || loading}
              onClick={() => setPage(Math.min(history.total_pages, page + 1))}
            >
              Next →
            </button>
          </div>
        </>
      ) : (
        <div className="history-loading">
          <div className="loader" />
          Loading pull history…
        </div>
      )}
    </section>
  );
}

function PullTable({
  rows,
  rarityFilters,
  toggleRarity,
}: {
  rows: Pull[];
  rarityFilters: Set<number>;
  toggleRarity: (rarity: number) => void;
}) {
  return (
    <div className="history-table-wrap">
      <table className="history-table">
        <thead>
          <tr>
            <th className="roll-column">Roll#</th>
            <th>Cards</th>
            <th>Rarity</th>
            <th className="banner-header-cell">
              <div className="rarity-filters" aria-label="Filter pull history by rarity">
                {[3, 4, 5].map((rarity) => (
                  <button
                    type="button"
                    key={rarity}
                    className={`rarity-filter-button rarity-${rarity} ${rarityFilters.has(rarity) ? "active" : ""}`}
                    aria-pressed={rarityFilters.has(rarity)}
                    onClick={() => toggleRarity(rarity)}
                    title={`${rarity}★ ${rarityFilters.has(rarity) ? "shown" : "hidden"}`}
                  >
                    {rarity}★
                  </button>
                ))}
              </div>
              <span>Banner</span>
            </th>
            <th>Time</th>
          </tr>
        </thead>

        <tbody>
          {rows.map((pull) => (
            <tr key={pull.id}>
              <td className="roll-number">#{pull.pull_order}</td>
              <td>
                <div className="result-cell">
                  <div className={`mini-avatar rarity-${pull.rarity} artwork-avatar`}>
                    <ArtworkImage
                      kind="card"
                      id={pull.item_id}
                      alt=""
                      className="artwork-fill"
                    />
                    <span className="artwork-fallback">{initials(pull.item_name)}</span>
                  </div>
                  <strong>
                    {characterNameFromCardId(pull.item_id)
                      ? `${characterNameFromCardId(pull.item_id)} - ${pull.item_name}`
                      : pull.item_name}
                  </strong>
                </div>
              </td>
              <td>
                <span className={`rarity-badge rarity-${pull.rarity}`}>
                  {pull.rarity}★
                </span>
              </td>
              <td>
                <span className="banner-name">{pull.banner.name}</span>
              </td>
              <td>{prettyDate(pull.pulled_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length === 0 && (
        <div className="filtered-empty">
          No pulls match the selected banner and rarity filters.
        </div>
      )}
    </div>
  );
}


function inferPreviewRarity(pull: SiteImportFile["pulls"][number]) {
  if (pull.rarity) return pull.rarity;

  const match = String(pull.card_id || "").match(/^card-[^-]+-(\d+)-/i);
  return match ? Number(match[1]) : 0;
}

function previewTime(pull: SiteImportFile["pulls"][number]) {
  if (pull.pulled_at) {
    const parsed = new Date(pull.pulled_at);
    return Number.isNaN(parsed.valueOf()) ? null : parsed;
  }

  if (!pull.draw_time) return null;

  let seconds = Number(pull.draw_time);
  while (seconds > 4_102_444_800) seconds = Math.floor(seconds / 1000);

  const parsed = new Date(seconds * 1000);
  return Number.isNaN(parsed.valueOf()) ? null : parsed;
}

function ImportView({
  preview,
  filename,
  error,
  importing,
  result,
  onFile,
  onImport,
  onViewDashboard,
}: {
  preview: SiteImportFile | null;
  filename: string;
  error: string;
  importing: boolean;
  result: SiteImportResult | null;
  onFile: (file: File | null) => void;
  onImport: () => void;
  onViewDashboard: () => void;
}) {
  const [localCommand, setLocalCommand] = useState("");
  const [localTokenExpiry, setLocalTokenExpiry] = useState("");
  const [localTokenLoading, setLocalTokenLoading] = useState(false);
  const [localTokenError, setLocalTokenError] = useState("");
  const [copiedCommand, setCopiedCommand] = useState(false);

  const stats = useMemo(() => {
    if (!preview) return null;

    const rarityCounts = new Map<number, number>();
    const banners = new Set<string>();
    const times: Date[] = [];

    for (const pull of preview.pulls) {
      const rarity = inferPreviewRarity(pull);
      rarityCounts.set(rarity, (rarityCounts.get(rarity) || 0) + 1);
      banners.add(pull.game_banner_id || pull.gacha_name);

      const time = previewTime(pull);
      if (time) times.push(time);
    }

    times.sort((a, b) => a.valueOf() - b.valueOf());

    return {
      total: preview.pulls.length,
      banners: banners.size,
      five: rarityCounts.get(5) || 0,
      four: rarityCounts.get(4) || 0,
      three: rarityCounts.get(3) || 0,
      oldest: times[0] || null,
      newest: times[times.length - 1] || null,
    };
  }, [preview]);

  async function generateLocalCommand() {
    setLocalTokenLoading(true);
    setLocalTokenError("");
    setCopiedCommand(false);

    try {
      const response = await fetch(`${API}/api/import/local-token`, {
        credentials: "include",
      });

      const payload = await response.json();

      if (!response.ok) {
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : `Token request failed (${response.status})`,
        );
      }

      const token = payload as LocalImportToken;
      setLocalCommand(
        buildPublicPowerShell(
          token.token,
        ),
      );
      setLocalTokenExpiry(token.expires_at);
    } catch (err) {
      setLocalTokenError(
        err instanceof Error ? err.message : "Could not generate local import token.",
      );
    } finally {
      setLocalTokenLoading(false);
    }
  }

  async function copyLocalCommand() {
    if (!localCommand) return;

    try {
      await navigator.clipboard.writeText(localCommand);
      setCopiedCommand(true);
    } catch {
      setCopiedCommand(false);
    }
  }

  return (
    <section className="import-layout">
      <div className="panel local-import-panel">
        <div className="local-import-heading">
          <div>
            <p className="eyebrow">POWERSHELL IMPORT</p>
            <h2>Import directly from hololive Dreams</h2>
            <p className="muted">
              Generate a short-lived PowerShell script, copy it, paste it into Windows
              PowerShell, and press Enter. No downloads, GitHub clone, Python, irm, or iex.
            </p>
          </div>
          <button
            type="button"
            className="primary-import-button compact local-token-button"
            disabled={localTokenLoading}
            onClick={generateLocalCommand}
          >
            {localTokenLoading ? "Generating…" : "Generate PowerShell"}
          </button>
        </div>

        {localCommand && (
          <div className="powershell-ready">
            <div>
              <strong>PowerShell ready</strong>
              <span>Click Copy PowerShell, paste it into Windows PowerShell, then press Enter.</span>
            </div>
            <button type="button" className="page-button" onClick={copyLocalCommand}>
              {copiedCommand ? "Copied!" : "Copy PowerShell"}
            </button>
            <details className="powershell-preview">
              <summary>View PowerShell</summary>
              <pre><code>{localCommand}</code></pre>
            </details>
          </div>
        )}

        {localTokenExpiry && (
          <p className="local-token-expiry">
            This PowerShell script expires {prettyDate(localTokenExpiry)}. Generate a
            fresh one next time you import.
          </p>
        )}

        {localTokenError && <div className="error-box import-error">{localTokenError}</div>}
      </div>

      <div className="manual-import-label">
        <span>MANUAL JSON FALLBACK</span>
      </div>

      <div className="import-grid">
        <div className="panel import-upload-panel">
          <p className="eyebrow">STEP 1</p>
          <h2>Select a Holodori pull export</h2>
          <p className="muted import-copy">
            You can still import a saved normalized JSON export manually. This is useful
            for reviewing or debugging an import before it is submitted.
          </p>

          <label className="file-drop">
            <input
              type="file"
              accept=".json,application/json"
              onChange={(event) => onFile(event.target.files?.[0] || null)}
            />
            <div className="file-drop-icon">⇧</div>
            <strong>{filename || "Choose JSON export"}</strong>
            <span>Nothing is uploaded until you review the preview and press Import.</span>
          </label>

          <div className="upload-safety">
            <span>Uploaded</span>
            <strong>Region · game account ID · banner · card · rarity · draw time</strong>
          </div>
          {error && <div className="error-box import-error">{error}</div>}
        </div>

        <div className="panel import-preview-panel">
          <div className="panel-title-row">
            <div>
              <p className="eyebrow">STEP 2</p>
              <h2>Review before upload</h2>
            </div>
            {preview && <span className="soft-badge">Ready</span>}
          </div>

          {!preview || !stats ? (
            <EmptyState text="Choose an export to preview exactly what will be sent." />
          ) : (
            <>
              <div className="import-account-summary">
                <div>
                  <span>Account</span>
                  <strong>{preview.account_nickname || preview.game_account_id}</strong>
                  <small>{preview.game_account_id}</small>
                </div>
                <div className="region-pill import-region">{preview.region}</div>
              </div>

              <div className="import-stat-grid">
                <ImportMetric label="Pulls" value={stats.total} />
                <ImportMetric label="Banners" value={stats.banners} />
                <ImportMetric label="5★" value={stats.five} accent="gold" />
                <ImportMetric label="4★" value={stats.four} accent="purple" />
                <ImportMetric label="3★" value={stats.three} accent="blue" />
              </div>

              <div className="import-range">
                <div>
                  <span>Oldest pull</span>
                  <strong>{stats.oldest ? prettyDate(stats.oldest.toISOString()) : "Unknown"}</strong>
                </div>
                <div>
                  <span>Newest pull</span>
                  <strong>{stats.newest ? prettyDate(stats.newest.toISOString()) : "Unknown"}</strong>
                </div>
              </div>

              <button
                type="button"
                className="primary-import-button"
                disabled={importing}
                onClick={onImport}
              >
                {importing ? "Importing…" : `Import ${stats.total} pulls`}
              </button>
            </>
          )}
        </div>
      </div>

      {result && (
        <div className="panel import-result-panel">
          <div className="result-check">✓</div>
          <div>
            <p className="eyebrow">IMPORT COMPLETE</p>
            <h2>{result.new_pulls} new pulls added</h2>
            <p className="muted">
              {result.duplicate_pulls} duplicate{result.duplicate_pulls === 1 ? "" : "s"} ignored
              out of {result.total_pulls} submitted pulls.
            </p>
          </div>
          <button type="button" className="primary-import-button compact" onClick={onViewDashboard}>
            View dashboard
          </button>
        </div>
      )}
    </section>
  );
}

function ImportMetric({
  label,
  value,
  accent = "",
}: {
  label: string;
  value: number;
  accent?: string;
}) {
  return (
    <div className={`import-metric ${accent}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function StatCard({
  label,
  value,
  hint,
  accent = "",
}: {
  label: string;
  value: string | number;
  hint: string;
  accent?: string;
}) {
  return (
    <div className={`stat-card ${accent}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{hint}</small>
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return (
    <div className="empty-state">
      <div className="empty-orb">✦</div>
      <p>{text}</p>
    </div>
  );
}



