# Stellaris Integration: Mod + Companion Process for an External Adaptive AI Engine

Research date: 2026-10-18. Tool-call budget was exceeded (about 20 calls) because several key sources were thin, so several questions remain open. Every item is tagged VERIFIED (read from a fetched source this session), PARTIAL (a source exists but does not fully confirm the claim), or UNVERIFIED (from background knowledge, not checked this session). Treat UNVERIFIED items as leads to test in-game, not as facts.

## Key Question 1: Current version, patch history, and mod structure

### Takeaway
The newest Stellaris release I could verify is 4.4.4 (June 24, 2026). I found nothing for July through October 2026, so the current version as of Oct 18, 2026 is UNVERIFIED. The 4.3.x and 4.4.x patches contain no AI-logic changes that matter for an external engine. The mod folder layout and `descriptor.mod` format are verified from the Paradox wiki.

### Cited Findings
- Release timeline: 4.3 "Cetus" released Mar 26, 2026; 4.3.1 Mar 19 (checksum f646 per URL); 4.3.6 May 11, 2026, checksum 6ccb — [patched.gg 4.3.6](https://patched.gg/games/stellaris/stellaris-436-patch-released-checksum-6ccb); 4.3.7 hotfix May 11, checksum 4f3c — [patched.gg 4.3.7](https://patched.gg/games/stellaris/stellaris-437-hotfix-patch-released-checksum-4f3c) (listed in search, not fetched); 4.4.2 Jun 15 — [patched.gg 4.4.2](https://patched.gg/games/stellaris/stellaris-442-hotfix-released-42fe) (listed, not fetched); 4.4.3 Jun 17 — [patched.gg 4.4.3](https://patched.gg/games/stellaris/stellaris-443-hotfix-released-0a3d) (listed, not fetched); 4.4.4 Jun 24, 2026 — [patched.gg 4.4.4](https://patched.gg/games/stellaris/stellaris-444-patch-released-5505) (VERIFIED, fetched). The 4.4.4 page does not list a checksum; the URL slug ends in "5505", which may be one, but this is UNVERIFIED.
- 4.3.6 multiplayer notes: fixed Out-of-Sync errors tied to `CS_SPECIES_COUNT`/`CS_SPECIES_INDEX` (nascent species) and `CS_COUNTRY_RESOURCES` — [patched.gg 4.3.6](https://patched.gg/games/stellaris/stellaris-436-patch-released-checksum-6ccb) (VERIFIED). Relevance: these are the kinds of OOS sources an external engine could trigger if it writes state the game does not expect.
- 4.3.6 mod-relevant note: the "very old age" logic is now a fixed define value of 1.2 (death more likely past 120% of species lifespan), so this is not a tunable script value in that version — [patched.gg 4.3.6](https://patched.gg/games/stellaris/stellaris-436-patch-released-checksum-6ccb) (VERIFIED as stated; implication for mod-tunability is an inference).
- 4.4.4 AI-related bullets (VERIFIED, [patched.gg 4.4.4](https://patched.gg/games/stellaris/stellaris-444-patch-released-5505)): "AI will now consider abandoning a contract after 20 years have passed since acceptance" (Nomadic Empires); fixed a crash "while the AI was evaluating which contract to take on"; nomadic empire randomization changed "to prevent them from occupying most advanced AI slots." The page has no changes to AI budget, AI war, ship designs, defines, scripting, or multiplayer OOS.
- Mod folder locations (VERIFIED, [Paradox wiki Modding](https://stellaris.paradoxwikis.com/Modding)): Windows `...\Documents\Paradox Interactive\Stellaris\mod`; Linux `~/.local/share/Paradox Interactive/Stellaris/mod`; macOS `~/Documents/Paradox Interactive/Stellaris/mod`. Steam Workshop content lives in `...\SteamApps\workshop\content\281990\<workshop id>`.
- `descriptor.mod` (VERIFIED, same page): required by the 2.4+ launcher, ignored by older launchers. Fields: `name` (required), `dependencies`, `picture` (must be `thumbnail.png` for the 2.4 launcher), `tags` (max 10), `version`, `supported_version` (the last number may be `*`), `remote_file_id` (launcher-generated, ignore). `path` is ignored in `descriptor.mod`.
- Load order (VERIFIED, same page): files load in ASCIIbetical order of filename. Same-name files follow launcher display order. Before patch 2.5 the order used the reverse of the mod display name. Overrides follow FIOS, LIOS, or DUPL behavior depending on folder; the wiki calls this undocumented and subject to change. Use unique filename prefixes.
- Achievements (PARTIAL, wiki summarized by a fetch tool): the `achievements.txt` entry says achievements are disabled for "common" changing or expanding mods anyway. Checksum and Ironman implications were not visible in the fetched text (the page was truncated).
- Steam Workshop folder naming and the launcher thumbnail rule come from the same wiki page.

### Inferences
- An external-engine mod that changes `common/` (AI weights, ai_budget, economic_plans) will almost certainly change the checksum and cannot be used on a server or MP session with unmodded clients. Only a pure-local, single-player mod that adds no new content is likely to avoid checksum issues, and even that is UNVERIFIED.
- Because load order is by filename and overrides are folder-dependent, any override of vanilla AI files needs a prefix strategy and a compatibility test against Workshop mods that also touch AI.

### Gaps
- Current version as of Oct 18, 2026: not found. The newest verified release is 4.4.4 (Jun 24, 2026). Check the Paradox forums or patched.gg for anything after that.
- Ironman, achievement, and checksum specifics: the wiki text was not fully read.
- Whether the Steam Workshop and the Paradox Launcher differ in how they handle `.mod` edits, beyond the wiki note to close the launcher before editing thumbnails.

---

## Key Question 2: Moddable AI surfaces, and what is hardcoded

### Takeaway
The wiki confirms that AI behavior is driven by data files (`ai_budget`, weights in `decisions`/`edicts`, `economic_plans`, `attitudes`, `ai_espionage`). It does not confirm the defines section or whether fleet movement and war timing are scriptable. Those remain open, and I did not verify them this session.

### Cited Findings
- Confirmed AI-related folders (PARTIAL, [Paradox wiki Modding](https://stellaris.paradoxwikis.com/Modding), summarized by a fetch tool): `ai_budget` determines what fractions of its resources the AI wants to spend. `ai_espionage` contains `operations` and `spynetworks` subfolders (both LIOS), with an initialization-order dependency for `spynetworks`. `attitudes` defines AI attitudes. `decisions` and `edicts` include AI weights. `economic_plans` sets AI resource income goals; fields in a same-named block overwrite existing fields and omitted fields keep their original values.
- The 4.3.6 "very old age" value being a fixed define (1.2) shows that some AI-adjacent behavior is controlled by defines, not by scripts ([patched.gg 4.3.6](https://patched.gg/games/stellaris/stellaris-436-patch-released-checksum-6ccb)).
- Console `effect`, `event`, and `trigger` commands exist and can test scripts in-game (VERIFIED, [Paradox wiki Console commands](https://stellaris.paradoxwikis.com/Console_commands)): `event [event id] [target id]`; `effect <name> = ...`; `trigger <file>` "tests a trigger script (*.txt) when placed in the Paradox Interactive\Stellaris\ folder"; `trigger_file` "tests the trigger script, and crashes the game."

### Inferences
- The ai_budget, ai weights, economic_plans, and attitude files are the cheapest levers for shaping AI decisions. They are data-driven and load at game start (load timing UNVERIFIED).
- Fleet movement, targeting, and war-declaration timing: UNVERIFIED. My background understanding is that these are largely code-driven, with only indirect control through war goals, `ai_will_do` weights, and fleet templates, but I did not confirm this. Test in-game before designing around it.

### Gaps
- `ai_will_do` blocks, `common/personalities`, `common/global_ship_designs`, `common/economic_plans` full schema, war-goal and diplomacy weight syntax: not fetched.
- NAI section of `common/defines/00_defines` (AI tuning constants): not fetched; the fetch of the AI modding wiki page failed or was truncated. The 4.3.6 "very old age" change is one data point showing defines carry gameplay values.
- Scripted effects/triggers, `on_actions`, `script_values`, and country flags/variables are standard Clausewitz script features; their availability in Stellaris is UNVERIFIED in this session.
- Whether AI empires' fleet and war decisions can be steered by a flag or variable an external process sets: not researched (depends on Key Question 4).

---

## Key Question 3: Data OUT of the game (observing state to an external process)

### Takeaway
The two verified channels are the in-game `log`/`debug` output and the save file. The save-file route is the one community tools use in practice. Latency and format details were not fully verified: the stellaris-dashboard repo confirms a Rust parser and reading saves while the game runs, but not the exact parse path or timing.

### Cited Findings
- The stellaris-dashboard tool reads save files while the game runs (VERIFIED, [GitHub eliasdoehne/stellaris-dashboard](https://github.com/eliasdoehne/stellaris-dashboard)). Its packaged app "tracks new saves as they appear while it runs." Bulk import of existing saves is available from a source checkout via `uv run stellarisdashboardcli parse-saves`. It exposes an autosave interval, a "Skip saves" option, and a CPU-thread setting for reading saves. The README says to "play the game until at least one autosave is triggered while the dashboard is running."
- Parser: the repo says a Rust toolchain is "needed to compile the `rust_parser` save-file parser" (built with maturin). MIT license, Copyright Elias Doehne (2018). The repo has 593 commits on master. Stellaris support version list not stated (VERIFIED from the fetch summary).
- Names are stored as templates "since Stellaris 3.4" (same README).
- No latency figures were published in the README (VERIFIED absence).
- The console `run` and `effect` commands also cover reading state only indirectly; no verified in-game read-back command for arbitrary variables was found.

### Inferences
- Autosave cadence is the practical latency bottleneck: a companion process reading saves sees state only at autosave points (typically in-game years, configurable). Exact autosave interval and file format details are UNVERIFIED.
- Save files are zip archives containing the Clausewitz-style text gamestate (UNVERIFIED from background knowledge; the dashboard's Rust parser is consistent with this but not confirmed).

### Gaps
- The `log` effect and what it writes to `game.log` or `error.log` in `Documents/Paradox Interactive/Stellaris/logs`: not verified. Background knowledge suggests scripts can write to logs, but I did not confirm the effect name or the path.
- Jomini crate, Rakaly, and pyradox: the Rakaly GitHub fetch failed. Jomini (Rust) search returned only unrelated results (Antoine-Henri Jomini the military theorist). Treat the Jomini/Rakaly parser claims as UNVERIFIED and check their repos directly.
- Measured parse time and autosave latency numbers: none found.

---

## Key Question 4: Data IN to the game (influencing a running game)

### Takeaway
A verified in-game `run` console command exists. It executes a list of console commands from a file in the Stellaris user folder. This is the most promising data-in channel from an external process. The remaining channels (save-edit-reload, polling flags, keystroke injection, DLL injection) are unverified or high-risk. Whether `run` works in MP, with achievements enabled, or without debug mode was not checked.

### Cited Findings
- `run` (VERIFIED, [Paradox wiki Console commands](https://stellaris.paradoxwikis.com/Console_commands)): "Runs the specified file with list of commands. File should be placed in the root Stellaris folder in your My Documents." Example: `run group_commands.ini`. The wiki places this in the Documents/Paradox Interactive/Stellaris folder (inference: the root of the user data folder, not the install directory).
- `trigger <file>`: tests a trigger script (`*.txt`) placed in `Paradox Interactive\Stellaris\` (VERIFIED, same page).
- `filewatcher`: "Toggles filewatcher" (VERIFIED as a name only). Whether it reloads script files at runtime was not visible in fetched text, so UNVERIFIED.
- `reload` family: `reload main.gui`, `reloadfx`, `reload_galaxy`, `reload_graphical_map`, `switchlanguage l_english` (VERIFIED). These reload assets, GUI, and localisation, not gameplay scripts. `reload_galaxy` starts a new game.
- `event` and `effect` console commands run content in-game (VERIFIED).

### Inferences
- A companion process could write a `.ini`/`.txt` command file and invoke `run` through the console to apply effects. This requires a way to trigger the console command from outside the game (keystroke injection or a pre-set hook). That trigger mechanism is UNVERIFIED. The `run` command itself needs a console-enabled session; whether the console is available in normal play or only with debug/cheat settings is UNVERIFIED.
- Changes to ai_budget, economic_plans, and other `common/` data files are read at load, so "data IN" through those files means edit-and-reload (new game or restart). This is not a live channel.
- Save-edit-reload loop: editing a save file and reloading it is feasible in principle but would be slow and fragile. Not verified against the current format or checksum rules.

### Gaps
- Whether `run` can be invoked from a file-based or external trigger without keystrokes: not verified.
- Polling-event pattern (an event that reads a flag set externally): the flag cannot be set from outside the game except through the channels above, so this depends on `run` or a save edit. Not verified.
- Memory or DLL injection: not researched in depth; it carries serious ToS, anti-cheat, and multiplayer risk, and I do not recommend it. Not verified.

---

## Key Question 5: Existing AI overhaul mods

### Takeaway
I could not verify StarNet AI's current status. The repo exists with 175 commits, but its README is one line and it states no supported version, license, or technique. No other AI overhaul mod was researched in enough depth to report technique.

### Cited Findings
- StarNet AI repo: [GitHub OldEnt/stellaris-starnet-ai](https://github.com/OldEnt/stellaris-starnet-ai) (VERIFIED as a repo with 175 commits). README text is only "StarNet AI mod for Stellaris." The repo's top-level folders are `common`, `events`, `gfx/interface`, `interface`, and `localisation`, plus `descriptor.mod`. No supported version, last-commit date, or license was visible. No technique (external process, Python, file-based communication) is described in the fetched content.
- Steam Workshop changelog pages for StarNet-related IDs exist (listed in search, not fetched), and a Nexus Mods page for Stellaris mod 118 appeared in search (contents not checked).

### Inferences
- Because StarNet's folder layout is data-only (`common`, `events`, `interface`, `localisation`) and shows no obvious external-process component in the top-level listing, it probably works entirely in script. This is an inference from folder names, not from code.

### Gaps
- StarNet last supported version, license, and whether it contains any external component: not verified. Read the repo's file tree and commit history directly (for example via `gh`) before reusing any code.
- Glavius AI, StarTech AI, Mercurial AI, and other AI overhauls: not researched this session. Their techniques are UNVERIFIED.
- Dev diary 385 "AI Benchmarks" appeared in search results (admin-forum.paradoxplaza.com), not fetched. It may describe internal AI tuning and is a lead for Key Question 2.

---

## Key Question 6: Multiplayer, OOS, and save-corruption risk

### Takeaway
Stellaris uses checksums for multiplayer compatibility, and recent patches still fix OOS errors tied to specific data. Any external write path that changes state the game does not expect can cause OOS or corruption. The specific risk model for an external engine is an inference from these facts.

### Cited Findings
- 4.3.6 fixed OOS errors tied to `CS_SPECIES_COUNT`, `CS_SPECIES_INDEX`, and `CS_COUNTRY_RESOURCES` (VERIFIED, [patched.gg 4.3.6](https://patched.gg/games/stellaris/stellaris-436-patch-released-checksum-6ccb)).
- Each patch publishes a checksum (for example 6ccb for 4.3.6, 171a for 4.3.2, 456f for 4.3.3, 4f3c for 4.3.7; VERIFIED from URLs and titles).
- Achievements are disabled for mods that change "common" content (PARTIAL, wiki summarized, see Key Question 1).

### Inferences
- Any companion process that changes galaxy state through `run` or save edits is a source of desync in multiplayer. It should be restricted to single-player or to clearly flagged single-player-only sessions.
- Save edits can corrupt saves if the Clausewitz text format is not reproduced exactly. Keep backups and validate against the vanilla format.
- Mods that change `common/` files will alter the checksum; players on other checksums will be unable to join the same session.

### Gaps
- Actual save corruption and OOS behavior for externally edited saves: not researched.
- Ironman implications: not verified.

---

## Capability Matrix (summary, status tagged)

| Capability | Mechanism | Status |
|---|---|---|
| (1) Observe game state | Autosave + external save parse (stellaris-dashboard) | Parsing verified as a community practice; latency numbers and format details UNVERIFIED |
| (1) Observe game state | In-game `log` effect to game.log / error.log | UNVERIFIED (not confirmed this session) |
| (2) Data out | Save file parse (zip + text gamestate, Rust parser in dashboard) | VERIFIED as used by dashboard; format details UNVERIFIED |
| (2) Data out | Console `debug_dumpevents`, `debug_stats`, `error` | VERIFIED as commands; output path UNVERIFIED |
| (3) Decisions in | Console `run <file>` executing a command list from the user Stellaris folder | VERIFIED as a command; external trigger and availability in normal play UNVERIFIED |
| (3) Decisions in | Edit `common/` data files | Load-time only; requires restart or new game (partial, wiki implies load-time) |
| (3) Decisions in | Save edit and reload | UNVERIFIED, fragile, high OOS and corruption risk |
| (3) Decisions in | Keystroke injection to console | UNVERIFIED, fragile |
| (3) Decisions in | Memory or DLL injection | Not researched; high ToS, anti-cheat, and MP risk; not recommended |
| (4) Override AI | `ai_budget`, `economic_plans`, `attitudes`, `decisions`/`edicts` AI weights | PARTIAL (folders confirmed on wiki; weight syntax not read) |
| (4) Override AI | Defines (NAI section) | UNVERIFIED (4.3.6 shows defines hold gameplay values) |
| (4) Override AI | Fleet movement, targeting, war-declaration timing | UNVERIFIED; background suggests largely hardcoded |

## Recommended next steps (for the report writer or a follow-up research pass)
1. Confirm the current Stellaris version as of Oct 2026 on the Paradox forums or patched.gg.
2. Read the wiki AI modding page and the defines page directly to confirm NAI and `ai_will_do`.
3. Test the `run` command in-game in a single-player session: confirm the file location, whether it works without debug mode, and whether it can be triggered from outside the game.
4. Test whether `filewatcher` reloads `common/` or events at runtime.
5. Inspect StarNet's file tree, commit history, and license with `gh` before reusing any code.
6. Verify the Jomini/Rakaly parser claims from their GitHub repos.
