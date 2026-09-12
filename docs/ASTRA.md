# GPT-6-Astra in Codex App and Codex CLI

`.codex/config.toml` is the only Codex Nexus settings source. It describes
host configuration for Codex App and Codex CLI. It is not an API request body,
and it does not override managed, session, or task settings that have higher
precedence.

## Operating defaults

The source requests:

| Area | Default |
| --- | --- |
| Lead model | `gpt-6-astra` |
| Lead and plan effort | `ultra` |
| Worker model and effort | `gpt-6-astra` and `max` |
| Delegation | Native V2 multi-agent support |
| Worker ceiling | A positive finite ceiling of `1_000_000` |
| Shell and execution | Native shell and unified execution enabled |
| Apps | Enabled, with destructive and open-world defaults disabled |
| Web search | `live` |
| Approval and sandbox | `never` and `danger-full-access` |
| Login shell | Disabled |
| Context management | Experimental context management enabled |
| Feature selection | Only the explicit values listed below |

Lead and planning work use Ultra. Workers use the same `gpt-6-astra` model at
Max. These are the owner's preferences; a task or higher-priority host setting
can select another supported effort. Delegate independent work when it helps
and verify the integrated result. The worker ceiling is not a staffing target,
unlimited concurrency, or an account entitlement. Host resources, service
limits, task authority, and the active session determine what can run.
See the [model guidance](https://learn.chatgpt.com/docs/models) and
[subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Explicit feature selection

The source contains these feature values:

| Configuration key | Value |
| --- | --- |
| `features.apps` | `true` |
| `features.fast_mode` | `true` |
| `features.memories` | `true` |
| `features.multi_agent_v2` | `true` |
| `features.prevent_idle_sleep` | `true` |
| `features.shell_tool` | `true` |
| `features.shell_zsh_fork` | `false` |
| `features.unified_exec` | `true` |
| `features.context_management.experimental_mode` | `true` |
| `features.network_proxy.enabled` | `true` |

This list records the owner's selection. A client feature catalog is an
inventory for compatibility checks, not a desired configuration. A newly
advertised experiment does not become a package requirement, and a removed
source selection must not be restored by setup, runtime inspection, or a
catalog refresh. Change source values and their contract checks together.
Unspecified feature behavior remains under Codex and any other applicable
configuration layer; omission does not mean a feature is disabled everywhere.

`shell_zsh_fork = false` preserves the Windows compatibility choice. A client
can parse a platform-specific key without exercising its implementation.
Rerun the runtime checks after a client or configuration change, and use a
fresh task when actual startup or skill discovery must be established.

`features.fast_mode = true` permits service-tier selection when the client
and model support it. The source leaves `service_tier` unset so the user's
selection remains in control. See the
[official speed guidance](https://learn.chatgpt.com/docs/agent-configuration/speed).

The [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
describes experimental context management as notes and searchable history
for eligible ChatGPT sign-ins. It also defines memories and the network proxy
as separate features. Enabling the proxy does not establish filtering for
hosted tools such as web search, apps, or MCP. Requested settings and local
acceptance are distinct from observed task behavior.

Keep the context mechanisms' roles clear:

- Codex context management retains or retrieves conversation context.
- Codex Nexus checkpoints bind a handoff or resume to source hashes, pending
  work, and verifiers.
- Memory locates prior evidence. It is not current authority or proof.

The source leaves context-window size, compaction thresholds, numeric token
budgets, output limits, and verbosity unset. Codex owns these defaults.
Runtime output records advertised context values as observations; it does not
copy them into a second source or establish a quality gain.

## Setup and precedence

Run setup from the repository root:

```sh
python -B setup.py --dry-run
python -B setup.py
python -B setup.py --health
```

The default operation merges only the settings declared by `.codex/config.toml`
through the selected Codex native configuration writer. It preserves unrelated
user settings and creates a private backup before publishing a changed file.
Typed feature tables preserve optional fields that Codex Nexus does not own;
setup updates only its declared values. Removing a key from the source stops
the package from requesting that key. It is not a request to delete unrelated
values from a user's existing configuration.

Setup does not maintain workspace entries, trusted project lists, MCP settings,
plugins, credentials, or other personal integrations. The active task can
override project defaults. Inspect task and host state before claiming that a
file on disk describes the effective model, effort, permissions, or context
behavior.

The shared skill catalog is linked from `~/.agents/skills` to this repository's
`skills/` directory. Personal, system, and plugin skills remain outside this
catalog. The [official skills guidance](https://learn.chatgpt.com/docs/build-skills)
documents user discovery from `~/.agents/skills` and symlink support. Changes
to repository skill source need no synchronized copies. Restart Codex if a
changed skill does not appear, and check the task's skill list to establish
actual discovery.

## Inspect the installed client

Use the runtime command when the source configuration or client changes:

```sh
python -B -m nexus runtime
python -B -m nexus runtime --codex codex
```

Client selection is explicit override, desktop-managed executable, then PATH.
An invalid explicit selection fails without fallback. The inspector validates
the source, reads the bundled model catalog, and passes source values as
native `-c` overrides to bounded local feature probes. The feature check
therefore does not silently depend on an existing installation or project
trust. Version labels identify the client; capability observations determine
compatibility.

A pass means the source satisfies the package contract and the selected local
client advertises the requested model, effort levels, and feature values.
Unselected features can appear in observations without becoming required.
Missing or removed requested capabilities fail. Deprecated selections produce
a warning. Contradictory context limits fail; missing context values remain
unknown. No model response is generated by this check.

Native acceptance does not prove account access, effective session precedence,
server availability, a full model task, or every dependent integration.
`setup.py --health` separately checks installed links and owned settings against
the current source. Report the client and source identity used for an actual
run instead of carrying forward a historical pass as current evidence.

The [configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
[subagent guidance](https://learn.chatgpt.com/docs/agent-configuration/subagents),
and [skills guidance](https://learn.chatgpt.com/docs/build-skills) describe the
host interfaces. The [Astra API reference](../skills/astra-api-integration/references/official-contracts.md)
applies when a task builds a separate API client. API reasoning controls, host
reasoning effort, and context-management settings are separate interfaces.

For design evidence and study limits, see [RESEARCH.md](RESEARCH.md). For a
fair quality comparison, use [EVALUATION.md](EVALUATION.md). Configuration
alone does not establish an improvement.
