/** Curated release notes for the What's new dialog, newest first. Plain words; the first entry must match the app version (releases.test.ts). */
export type LucideIconName = "Palette" | "Laptop" | "Puzzle" | "ShieldCheck" | "KeyRound" | "Gauge" | "RefreshCw" | "Accessibility" | "Wrench" | "Plug" | "Cloud" | "ListChecks" | "ShieldAlert" | "Bug";

export interface ReleaseHighlight { icon: LucideIconName; title: string; text: string }
export interface Release { version: string; date: string; title: string; summary: string; image?: string; highlights: ReleaseHighlight[] }

export const RELEASES_URL = "https://github.com/0sec-labs/0security/releases";

export const RELEASES: readonly Release[] = [
  {
    version: "0.46.0",
    date: "2026-11-26",
    title: "Proof for findings, AI-security checks, and one product",
    summary: "Findings can carry an honest proof, and 0security checks the files AI coding tools read, models and look-alike packages. Foxguard now ships from this repository, and the cloud connection is gone.",
    highlights: [
      { icon: "Bug", title: "Proof and variants", text: "A finding can carry proposed steps labelled not run, or a result only when a harness ran it in a container. Verified findings record a root cause and seed a hunt for the same bug elsewhere. Results from ZAP, Strix and Nuclei import as findings." },
      { icon: "ShieldAlert", title: "AI-security checks", text: "0security sbom agent-config checks rules, MCP and settings files for hidden text, unpinned launchers and broad permissions. sbom generate --ai writes a CycloneDX ML-BOM, and sbom check --registry flags invented or planted packages." },
      { icon: "Plug", title: "Sentinels and Bitbucket", text: "Sentinels review Bitbucket pull requests, send findings to GitHub code scanning or a pull request comment, and have spend limits. Findings can be ranked by business context in every command that orders them." },
      { icon: "ShieldCheck", title: "Safer by default", text: "Probe refuses public and metadata targets unless you confirm the host. Fixes close gaps in SCIM tokens, role checks, the node permission layer and scanned-folder config. DO_NOT_TRACK=1 is the one telemetry switch, and Foxguard is built into the signed release." },
    ],
  },
  {
    version: "0.45.0",
    date: "2026-11-24",
    title: "Java projects, team sign-in, and signed releases",
    summary: "Scans, SBOMs and fixes understand Maven and Gradle projects, and Bitbucket, Jenkins, Azure and GitLab get CI recipes. A license key adds SAML, SCIM, roles, a shared findings server and a tamper-evident audit log. Releases are signed with cosign.",
    highlights: [
      { icon: "Wrench", title: "Fixes and findings for Java", text: "A scan keeps each batch inside one Maven or Gradle module. 0 findings fix bumps a vulnerable dependency without a model call, and 0 sbom generate reads a Maven or Gradle report you already ran. 0 findings list --context orders findings by what matters to your business." },
      { icon: "Plug", title: "Bitbucket, Jenkins, Azure and GitLab", text: "Sentinels review Bitbucket pull requests, and signed webhooks start a Sentinel from another system. CI recipes cover four pipelines. They are new and have not run on a live service. Azure OpenAI can sign in with Entra ID." },
      { icon: "KeyRound", title: "Team features with a license key", text: "A license key unlocks SAML sign-in, SCIM provisioning, custom roles, a shared findings server, audit retention and signed evidence. Checking the audit log and the evidence stays free and offline." },
      { icon: "ShieldCheck", title: "Signed releases and offline installs", text: "Checksums, the SBOM and container images are signed with cosign, and install.sh verifies the signature when cosign is installed. A Helm chart, an EKS example and an air-gapped bundle are included." },
    ],
  },
  {
    version: "0.44.0",
    date: "2026-11-23",
    title: "Say what matters, and scans you can trust to finish",
    summary: "Teams can declare which attack vectors they care about, and scans now retry, say why they stopped and check that they read the files. The command is now 0security, with 0 as a short alias.",
    highlights: [
      { icon: "ListChecks", title: "Declare what matters", text: "A Scope section in SECURITY.md says which vectors are in and out of scope. Findings outside it are listed apart and don't fail your build. 0 threat-model --interview asks specific questions about your code and your last scan, and writes the answers for you to review." },
      { icon: "Gauge", title: "Scans that finish and explain", text: "A scan retries when the model provider drops a connection, limits each batch's time, and names the real cause when it stops early. It sends one follow-up for files it never opened, and warns when a scan with no findings read only part of the code." },
      { icon: "Bug", title: "Cleaner findings", text: "Findings land in clearer categories such as broken access control, hardcoded secret and missing rate limit. The same flaw filed twice is merged, and a scan with no findings says that is not proof the code is safe." },
      { icon: "ShieldCheck", title: "0security, and safer helpers", text: "The command is installed as 0security with 0 as an alias, and the Action gains inputs for scanners, rules, tiers and scope. Extension scripts get a clean environment, the address check blocks more IPv6 forms, and helpers ask before each command in Ask mode." },
    ],
  },
  {
    version: "0.43.0",
    date: "2026-11-22",
    title: "Scan tiers, safer extensions, deeper dependency checks",
    summary: "Operators can set how deep each repository is scanned and compare a scan with other tools' findings. Extensions run in an OS sandbox, and Maven and Gradle projects resolve their dependency trees.",
    highlights: [
      { icon: "Gauge", title: "Scan tiers and second opinions", text: "An operator-owned tier map sets budget, depth or skip per repository. 0 scan --known tells the scan what other tools already found, and --only-new hides the repeats. 0 findings metrics shows what runs cost." },
      { icon: "Wrench", title: "Fixes you can check", text: "0 findings fix --sandbox docker runs your checks in a container with no network. Pull requests follow your title, branch and label conventions, and 0 findings export-fixes reports what was proposed and merged. Accepted risks now need a reason and an expiry." },
      { icon: "ShieldCheck", title: "Safer extensions and scanners", text: "Extension scripts and MCP servers can run in a macOS or Linux sandbox with the network off. Extension commands are checked more strictly, and the scanners ignore ignore-files and config inside the repository being scanned. Agent Skills folders load as skills." },
      { icon: "ListChecks", title: "Dependencies and operations", text: "0 sbom generate --resolve reads Maven and Gradle trees from your own build tool, and 0 sbom licenses checks a license policy. Sentinels start on releases and finished checks, and 0 config audit-export sends the audit log to a SIEM." },
    ],
  },
  {
    version: "0.42.0",
    date: "2026-11-22",
    title: "Fix findings, bring your own scanners, check dependencies",
    summary: "0 can now fix a finding as a pull request, import what other scanners found, review large repositories in batches, and check dependencies, licenses and model files.",
    highlights: [
      { icon: "Wrench", title: "Fix as a pull request", text: "0 findings fix makes one branch and one pull request per finding, as a draft when your checks fail. It never pushes to your base branch. 0 findings burndown works through a backlog in batches." },
      { icon: "Plug", title: "One list of findings", text: "Import SARIF from any scanner or a repository's GitHub alerts, run FoxGuard, Semgrep, Gitleaks, Trivy, OSV-Scanner or Checkov first, add your own Semgrep rules, and send findings to GitHub code scanning or Issues." },
      { icon: "ListChecks", title: "Large repositories", text: "A large scan runs in batches, can be continued with --resume and says how much was reviewed. It exits 3 when part was not reached; --allow-partial exits by findings only. 0 scan --plan shows the size and a rough cost first." },
      { icon: "ShieldCheck", title: "Dependencies, evidence and AI apps", text: "SBOMs carry hashes, the dependency graph and licenses. 0 sbom check shows fixed versions and flags malicious packages, 0 sbom licenses checks a license policy, and 0 findings evidence writes an evidence folder for CRA reviews. 0 threat-model, 0 findings verify and new experimental skills cover threat models, false positives, AI apps, cryptography and infrastructure code." },
    ],
  },
  {
    version: "0.41.0",
    date: "2026-11-19",
    title: "Scan in CI, and scan more than once",
    summary: "0 scan can run several passes and merge them, its SARIF now works in GitHub code scanning, and two new skills hunt variants of a fixed bug and broken assumptions.",
    highlights: [
      { icon: "ShieldCheck", title: "Several passes", text: "0 scan --passes 3 scans the same code three times and merges the findings, because one run misses issues another finds. The budget is split between the passes, and each finding says how many found it." },
      { icon: "Gauge", title: "Works in CI", text: "The SARIF has paths relative to the repository, the category as the rule, and a key that stays the same from run to run, so GitHub code scanning shows findings on the right lines. A job needs only an API key; the docs have a workflow." },
      { icon: "Puzzle", title: "Two new skills", text: "variant-hunting takes a fix and finds the same bug at the other places. assumption-mining lists what each function silently relies on and looks for a caller that breaks it." },
      { icon: "Bug", title: "Steadier scans", text: "A scan keeps going when a busy console misses a status request, leaves out node_modules and build folders, and never sends a git URL's password to the model." },
    ],
  },
  {
    version: "0.40.1",
    date: "2026-11-19",
    title: "Folders work in the standalone binary",
    summary: "A fix for the standalone 0, where every folder looked unreadable. If 0 scan, threads --folder or the folder picker said it couldn't read a folder, update.",
    highlights: [
      { icon: "Bug", title: "Folders can be read again", text: "In the downloaded binary, choosing a folder, threads start --folder and 0 scan failed with \"couldn't read that folder\". The folder check mishandled how the compiled program closes a directory. It works now." },
      { icon: "ShieldCheck", title: "0 scan from the binary", text: "0 scan starts its own console and scans a folder from the downloaded binary, the same as from a source checkout." },
      { icon: "Wrench", title: "A release check for it", text: "The release build now starts a console from the compiled binary and starts a thread in a folder, so a build that cannot read folders no longer ships." },
    ],
  },
  {
    version: "0.40.0",
    date: "2026-11-19",
    title: "Scan a code base, as a thread",
    summary: "0 scan is back: it scans a folder or git repository in a thread and prints the findings as a report. Findings, Extensions and Computers get a cleaner layout.",
    highlights: [
      { icon: "ShieldCheck", title: "0 scan", text: "Run 0 scan on a folder or a git URL. It lists every entry point, checks each one and records findings with file and line. Pick json, md, sarif, html or pdf, set a budget, and use --fail-on in CI. In chat, /scan does the same in the current thread." },
      { icon: "ListChecks", title: "One flaw, one finding", text: "Findings of a scan that point at the same lines are merged, and a scan no longer loses a finding to another scan's. A finding with an unknown category is kept as other." },
      { icon: "Palette", title: "Cleaner pages", text: "Findings is a full-width list with a page for each finding. Extensions and Computers are plain rows like Skills." },
    ],
  },
  {
    version: "0.39.0",
    date: "2026-11-18",
    title: "Sentinels and a smaller 0",
    summary: "Agents are now Sentinels with a side pane and a Profile dialog, the model has eight tools, and the scan engines, scope and budgets are gone.",
    highlights: [
      { icon: "ListChecks", title: "Sentinels", text: "One thread that keeps going, with its own prompt, triggers and memory. Its page has a side pane for status, triggers, instructions and memory, and a small Profile dialog." },
      { icon: "Wrench", title: "Eight tools", text: "A shell, file edit, findings, skills, memory, web search and sub-agents. Everything else is the shell and the 0 command line." },
      { icon: "Palette", title: "A smaller product", text: "The scan engines, scope, budgets and the legacy command names are removed. The commands are findings, threads, sentinels, skills, memory, models, computers and config." },
    ],
  },
  {
    version: "0.38.0",
    date: "2026-11-16",
    title: "One Memory, one Appearance",
    summary: "Memory is one set of plain notes you can review, Appearance is one page, and there are six new backgrounds.",
    highlights: [
      { icon: "ListChecks", title: "One Memory", text: "Plain notes per folder or global that threads read each turn. Notes a thread adds after reading untrusted content wait in a Proposed inbox until you accept them, and secrets are never saved." },
      { icon: "Palette", title: "Appearance in one place", text: "Settings > Appearance is one page, and six new backgrounds: Dither (flow), Contours, Glyph contours, Fish school, Plankton and Ink." },
      { icon: "Wrench", title: "One width everywhere", text: "Agents, Skills, Memory, Computers and Extensions line up on one page width. `0 memory export --to AGENTS.md` writes your notes as a file you can review." },
    ],
  },
  {
    version: "0.37.0",
    date: "2026-11-16",
    title: "Verified means checked",
    summary: "A fix is only called Verified when 0 ran a check for it, the Findings board is back, and the sidebar, Skills page and docs are simpler.",
    highlights: [
      { icon: "ShieldCheck", title: "Verified means checked", text: "A skill can declare a check that 0 runs in a container before anything is called Verified. Fixes and threads end with Verified by or Not verified, and the second-Agent check is now called Corroborated." },
      { icon: "ListChecks", title: "Findings board", text: "Switch Findings between List and Board and drag a finding between columns. The Agents list is back in the sidebar, next to a Threads toggle." },
      { icon: "Wrench", title: "Simpler and steadier", text: "The Skills page is a flat list with search, the docs are 12 pages, and many small errors are fixed, from raw reconnect messages to threads that kept the title New thread." },
    ],
  },
  {
    version: "0.36.0",
    date: "2026-11-15",
    title: "Extensions",
    summary: "Extensions replace plugins. They connect 0 to your tools and services: GitHub, MCP servers and rules like scope. Each is pinned to a commit, with one decision point before every command.",
    highlights: [
      { icon: "Puzzle", title: "Extensions", text: "Add an extension pinned to a commit with 0 skills add <repo>@<sha>. A prompt shows what it can do and the diff on update, and the new Extensions page replaces Explore." },
      { icon: "ShieldCheck", title: "Safer MCP servers and scope", text: "MCP servers live inside extensions and start on demand, every tool is pinned and blocked until you approve a change, and scope is on by default whenever a scope is set." },
      { icon: "Plug", title: "Plugins are gone", text: "The old plugin system, its registry and self_extend are removed. Installed plugins are kept disabled and imported once, and GitHub is now a built-in connector." },
    ],
  },
  {
    version: "0.35.0",
    date: "2026-11-15",
    title: "Nine commands, one mode",
    summary: "The CLI is down to nine commands, 0 opens the console, and Threads and Agents have shell access with a small core tool list.",
    highlights: [
      { icon: "ListChecks", title: "Nine commands", text: "Run 0 to open the console. scan, findings, threads, agents, skills, memory, models, computers and config cover the rest, and old commands still work for one release with a short moved note." },
      { icon: "ShieldCheck", title: "One mode with shell access", text: "Read-only and Can make changes are gone. Scope, budget, the sandbox and the Ask before running commands setting limit what Threads and Agents can do." },
      { icon: "Gauge", title: "Faster and steadier", text: "0 --version starts in about 0.15 seconds, Threads and Agents load a small core tool list, and a long-running console no longer fails every call after heavy use." },
    ],
  },
  {
    version: "0.34.0",
    date: "2026-11-15",
    title: "Updates that finish themselves",
    summary: "The console can update and restart itself, pull request Agents react to GitHub straight away in server mode, and plugin reviews show which tools changed.",
    highlights: [
      { icon: "RefreshCw", title: "Update and restart in one click", text: "The Update available card downloads, checks and installs the new version, then restarts and reconnects by itself. With Updates set to Automatic it installs in the background once nothing is running." },
      { icon: "Cloud", title: "Faster pull request Agents", text: "In server mode, GitHub can tell 0 about a new pull request the moment it opens, so an Agent starts reviewing without waiting for the next check." },
      { icon: "ShieldCheck", title: "See what changed in a plugin", text: "Reviewing a plugin update marks each tool as Changed, New or Removed, and four unused enterprise hooks are gone." },
    ],
  },
  {
    version: "0.33.0",
    date: "2026-11-15",
    title: "Two kinds of access",
    summary: "Threads, Agents, automations and scans are now Read-only or Can make changes, and asking first is one global setting.",
    highlights: [
      { icon: "ShieldCheck", title: "Two kinds of access: Read-only or Can make changes", text: "Agents and threads that asked first now follow one setting, Ask before running commands, which is off by default. Turn it on in Settings to keep confirmations." },
      { icon: "Wrench", title: "The same choice in the CLI", text: "Use --access read-only or --access changes on 0 agents create, 0 threads start and 0 lab console. The old flags still work." },
      { icon: "ListChecks", title: "Nothing is lost on upgrade", text: "Saved Threads, Agents and automations still load, and the first start shows one note in Settings > Helpers that you can dismiss." },
    ],
  },
  {
    version: "0.32.0",
    date: "2026-11-15",
    title: "Simpler approvals, calmer Threads",
    summary: "Threads run commands without asking by default, slash commands match the CLI, and the sidebar has seven items.",
    highlights: [
      { icon: "ShieldCheck", title: "Simpler approvals", text: "Threads and Agents run commands automatically. One setting, Ask before running commands, turns the questions back on. Agents are Read-only, Can run commands or Can open pull requests." },
      { icon: "Wrench", title: "Slash commands match the CLI", text: "/threads list, /findings list and /config get work like their 0 commands, and the old names still work." },
      { icon: "ListChecks", title: "A calmer Thread", text: "One shimmer for the live work, one row per call, clear messages when a send fails or a reply comes back empty." },
      { icon: "Palette", title: "Seven sidebar items", text: "Skills has its own page with Installed and Browse, real brand logos appear on Explore, and the Findings board drops cards where you let go." },
    ],
  },
  {
    version: "0.31.0",
    date: "2026-11-14",
    title: "Threads, Agents and a leaner CLI",
    summary: "Missions are now Threads, Sentinels are now Agents, and the CLI has 14 commands.",
    highlights: [
      { icon: "ListChecks", title: "Threads and Agents", text: "Missions are now Threads and Sentinels are now Agents. The old CLI names still work, and Memory has its name back." },
      { icon: "Wrench", title: "A leaner CLI", text: "14 top-level commands now cover threads, agents, automation, models, skills, memory and plugins, with the old names kept as aliases." },
      { icon: "KeyRound", title: "Connections that tell the truth", text: "A model shows Key rejected when its key does not work, and a failed message offers a working model to switch to." },
      { icon: "Cloud", title: "Run it on a server", text: "An experimental single-node server mode, a Docker image that serves the console, and anonymous usage and crash reports you can turn off." },
    ],
  },
  {
    version: "0.30.0",
    date: "2026-11-14",
    title: "Simpler navigation, GitHub sign-in",
    summary: "A shorter sidebar, a Models page, a guided GitHub connection and ready-made Automations.",
    highlights: [
      { icon: "ListChecks", title: "A shorter sidebar", text: "New mission, Findings, Automations and Explore, with Skills and Plugins inside Explore and Lessons under More." },
      { icon: "KeyRound", title: "Connect GitHub your way", text: "Reuse your GitHub CLI login, sign in with a code, or follow a guided token. You see what permissions are missing." },
      { icon: "Wrench", title: "Eight Automations to start from", text: "Pick a starting point such as a code review or a secrets check. Code reviews use your current folder." },
      { icon: "Cloud", title: "One Models page", text: "Providers and models live together at Models, and a switch hides cost and usage if you prefer." },
    ],
  },
  {
    version: "0.29.0",
    date: "2026-11-14",
    title: "A new look",
    summary: "A calmer console in the Capy style, with Themes, Computers and a skills directory.",
    highlights: [
      { icon: "Palette", title: "New look and Themes", text: "The console follows the Capy style with a neutral accent. Pick a Theme, or switch back to orange, in Settings." },
      { icon: "Laptop", title: "Computers and Sentinels", text: "Connect and manage the machines missions run on, and start from ten Sentinel presets such as Fix Engineer and Secrets Hunter." },
      { icon: "Puzzle", title: "Skills: Installed and Explore", text: "Browse a GitHub skills directory, preview a skill, and add it at a pinned version." },
      { icon: "Gauge", title: "Smoother everyday use", text: "Recommended models, key checks when you connect, visible retries on rate limits, and fixes for deleting and retrying missions." },
    ],
  },
  {
    version: "0.28.11",
    date: "2026-11-13",
    title: "Safer parallel work",
    summary: "Parallel workers no longer step on each other, and model costs are more accurate.",
    highlights: [
      { icon: "ShieldCheck", title: "Protected shared checkouts", text: "Workers that run side by side can read the same folder without overwriting each other's changes." },
      { icon: "Gauge", title: "Accurate model prices", text: "Together models keep their own prices, and incomplete usage receipts are rejected instead of guessed." },
      { icon: "RefreshCw", title: "Reliable live reload", text: "Development servers reload on the ports you choose and shut down cleanly when you stop them." },
      { icon: "Wrench", title: "Security updates", text: "Hono and its Node server adapter are updated for current security advisories." },
    ],
  },
  {
    version: "0.28.10",
    date: "2026-11-10",
    title: "Together AI and custom models",
    summary: "Connect Together AI and pick custom models from a list.",
    highlights: [
      { icon: "Cloud", title: "Together AI", text: "Connect Together AI with its own credentials and discover its models, including free ones." },
      { icon: "ListChecks", title: "Pick custom models", text: "After connecting an OpenAI-compatible service, choose a model from a dropdown. You can still type an ID by hand." },
      { icon: "Plug", title: "Claude Code tools", text: "Claude Code can use 0's tools through a private local bridge, without duplicate calls." },
      { icon: "Bug", title: "Fewer glitches", text: "Model lists follow the current key and endpoint, and force-stopping a mission keeps your history." },
    ],
  },
];

/** Release notes for the running app, if the newest entry matches the version. */
export const LATEST_RELEASE: Release = RELEASES[0]!;
