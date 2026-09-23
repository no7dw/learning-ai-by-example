# DSH Plugin Reply Flow Course

> A source-reading course for understanding how DeepSeek Harness handles plugin-owned slash commands and renders their replies.
>
> Code baseline: September 23, 2026. Repository: `/Users/dengwei/work/ai/github/deepseek-harness`.

## 1. The first distinction: two meanings of `plugin`

The repository uses the word `plugin` in two related but different ways:

1. **CLI plugin management**: `dsh plugin --profile <name> add <package>` installs a profile bundle or dependency.
2. **Runtime slash commands**: `/plan`, `/goal`, `/compact`, and similar commands are registered by plugins and execute directly against an Agent.

The visible “reply” after a slash command belongs to the second system. There is currently no built-in runtime `/plugin` command. The runtime design is generic: any plugin can register a command and return a `CommandResult`.

## 2. Learning goals

After this course, you should be able to:

1. Explain how a plugin registers a human-facing slash command.
2. Trace `/plan off` from the browser composer to the Host command handler.
3. Explain why the command result is not an `assistant/message`.
4. Describe how `command/run` and `command/done` form a durable pair.
5. Find the code responsible for command discovery, Remote transport, execution, and rendering.
6. Compare runtime command execution with `dsh plugin` CLI management.

## 3. Core mental model

A runtime slash command is a direct human control plane. It is not a prompt shortcut.

```text
User types /plan off
        |
        v
Browser composer
        |
        v
Slash-command UI source
        |
        v
Typed Remote RPC
        |
        v
Host command registry
        |
        v
Plugin-owned handler
        |
        v
CommandResult
        |
        +------------------------------+
        |                              |
        v                              v
Durable command events             Remote acknowledgment
command/run + command/done          back to the calling browser
        |
        v
Conversation projection
        |
        v
Visible command-flow node
```

The key property is:

```text
CommandResult
    -> command/done
    -> conversation command node
    -> visible UI reply

not:

CommandResult
    -> assistant/message
    -> model history
```

Therefore, a direct command reply does not enter the model context and does not consume model tokens by itself.

## 4. Full request and reply flow

Use `/plan off` as the example.

```text
1. User enters `/plan off`
          |
          v
2. ui-commands registers a `/` input source
   - discovers command descriptors
   - resolves `plan`
   - preserves the argument text ` off`
          |
          v
3. Browser calls:
   remote.commands.execute(sessionId, "/plan off", attachments)
          |
          v
4. Remote gateway routes the request to the Host
          |
          v
5. CommandRuntime.execute(agent, line, attachments, signal)
   - parse command
   - resolve agent-scoped or global definition
   - mint commandId
   - append command/run
          |
          v
6. Plugin handler runs
   - dsh-plan-mode changes or queues plan state
   - returns success or error
          |
          v
7. CommandRuntime appends command/done
   - kind: success | error
   - optional text
   - optional sourceEventSeq
          |
          +------------------------------+
          |                              |
          v                              v
8a. Calling browser receives       8b. Session event delivery sends
    CommandExecution                  command/run/done to clients
          |                              |
          v                              v
9a. Composer reports admission     9b. ui-chat command Definition folds
    success and does not echo           both events by commandId
          |                              |
          +---------------+--------------+
                          |
                          v
10. Chat renders one command node:
    `/plan off` -> `Plan mode off.`
```

## 5. What the Host command registry owns

The Host-side registry is `CommandRuntime` in:

```text
packages/interaction/commands/src/index.ts
```

The main execution method is `CommandRuntime.execute()`.

```text
line
  |
  v
parseCommand()
  |
  +-- invalid syntax ------------> undefined, no log event
  |
  v
resolve effective command
  |
  +-- unknown command -----------> undefined, no log event
  |
  v
mint commandId
  |
  v
append command/run
  |
  v
admit attachments, if declared
  |
  v
invoke handler
  |
  +-- throws/aborts -------------> command/done error, then reject
  |
  v
normalize CommandResult
  |
  v
append command/done
  |
  v
return CommandExecution
```

The registry deliberately does not create a model message. The handler receives an invocation containing the exact Agent, raw input, attachments, and cancellation signal.

## 6. Command definitions

A plugin registers a command through `ctx.commands.register()`:

```ts
ctx.commands.register({
  name: 'plan',
  description: 'Enter or leave plan mode',
  input: { hint: '[off|message]' },
  handler: ({ agent, rawInput }) => {
    // Directly changes plugin-owned Agent state.
    return { kind: 'success', text: 'Plan mode off.' }
  },
})
```

The important fields are:

| Field | Purpose |
| --- | --- |
| `name` | Lowercase command name without `/`. |
| `description` | Discovery text shown by the UI. |
| `input.hint` | Composer placeholder or input hint. |
| `input.attachments` | Whether images/files may be submitted. |
| `handler` | Direct plugin-owned operation. |
| `recordInput` | Whether raw input is duplicated in `command/run`. |
| `definitionId` | Stable plugin-owned identity for client presentation. |

The command name is parsed from the beginning of the line. Everything after the name, including separator whitespace, becomes `rawInput`.

## 7. The lifecycle event pair

Every admitted command gets one pairing ID:

```text
commandId = cmd-<instance-token>-<sequence>
```

The first event records admission and the second records settlement.

```json
{
  "type": "command/run",
  "data": {
    "commandId": "cmd-ab12-1",
    "name": "plan",
    "args": " off",
    "source": { "kind": "user" }
  }
}
```

```json
{
  "type": "command/done",
  "data": {
    "commandId": "cmd-ab12-1",
    "kind": "success",
    "text": "Plan mode off."
  }
}
```

The events are **log-only**. They are not model-surface messages and are not wrapped in an Agent turn.

This pairing supports several important behaviors:

```text
command/run only
    -> command is still executing

command/done only in a loaded window
    -> UI can still render a settled command with missing start data

run + done with same commandId
    -> UI updates one command node instead of creating two rows
```

## 8. Browser-side command design

The browser implementation is:

```text
packages/client/ui-commands/src/client/service.ts
```

`CommandUiRuntime` connects the slash trigger to the Host command Remote.

```text
ui-input-trigger
        |
        | trigger: `/`
        v
CommandUiRuntime
        |
        +--> candidates(): list commands and local contributions
        |
        +--> matchSpace(): claim commands that accept arguments
        |
        +--> matchEnter(): resolve and dispatch a complete line
        |
        +--> dispatch(): menu pick, popup/action, or direct execution
        |
        v
ctx.remote.commands.execute(...)
```

The UI supports three broad routes:

```text
Slash input
  |
  +-- client contribution ------> local popup or action
  |
  +-- Host command with input --> leadingInput claim, then submit args
  |
  +-- bare Host command --------> detached Host execution
```

The browser intentionally separates two kinds of failure:

```text
Admission failure
  unknown or malformed command
  -> immediate composer notice
  -> no command/run event

Handler result error
  command was admitted and handler returned error
  -> command/run + command/done are durable
  -> command node renders the error
```

For commands with attachments, the browser checks the declaration before submitting. The Host performs the authoritative attachment admission and passes frozen image/file blocks to the handler.

## 9. How the reply becomes a chat node

The conversation Definition is:

```text
packages/client/ui-chat/src/client/conversation-nodes/command.ts
```

Its folding logic is:

```text
command/run
    |
    v
commandFromRun()
    -> { name, args, outcome: null }
    |
    v
command/done with same commandId
    |
    v
commandFromDone()
    -> { outcome: { kind, text, sourceEventSeq } }
    |
    v
chatNode(..., 'command', ...)
```

The shared UI record type is `CommandNode` in:

```text
packages/client/ui-conversation/src/client/contract/records.ts
```

The node contains:

```text
kind       = "command"
commandId  = lifecycle correlation ID
name       = command name
args       = raw input after the name
outcome    = null while running, otherwise success/error data
```

This design lets the UI show command execution as a durable activity row while keeping it separate from user messages, assistant messages, and tool calls.

## 10. Example command producer: `/plan`

The Plan Mode plugin registers the command here:

```text
packages/plan/plan-mode/src/index.ts
```

The registration is activated through the `commands` injection:

```text
PlanModeController
        |
        v
ctx.inject(['commands'], ...)
        |
        v
commandCtx.commands.register({ name: 'plan', ... })
```

The handler can do more than return text. It can mutate domain state, append a domain event, or schedule work. The command registry only owns command admission and lifecycle logging. The command producer owns the business effect.

For `/plan`, the lifecycle is conceptually:

```text
/plan off
   |
   v
command/run
   |
   v
PlanModeController handler
   |
   +--> plan state change or queued intent
   |
   v
command/done: "Plan mode off."
   |
   v
command chat node
```

The plan plugin may also affect later model requests by changing the `plan:policy` system-prompt section. That later model-visible effect belongs to the Plan Mode package, not to the generic command registry.

## 11. Separate flow: CLI `dsh plugin`

The CLI command is not a chat command and does not return a `CommandResult`.

```text
dsh plugin --profile my-profile add some-package
        |
        v
apps/cli/src/args.ts
  parse CLI mode and profile
        |
        v
apps/cli/src/bin.ts
  dispatch mode === "plugin"
        |
        v
apps/cli/src/plugin.ts
  runPlugin(profile, args)
        |
        +--> initialize profile if missing
        |
        +--> run pnpm in profile directory
        |
        +--> inspect installed package manifests
        |
        +--> reconcile dsh.profile.bundles
        |
        v
next profile boot
  loads bundle cordis.patch.yml
```

This path manages persistent profile dependencies. It does not involve:

```text
ctx.commands
command/run
command/done
CommandResult
ui-chat command nodes
```

## 12. Source map

### Runtime command registry

| File | Role |
| --- | --- |
| `packages/interaction/commands/src/index.ts` | Registration, scoping, parsing, execution, lifecycle events, attachment admission. |
| `packages/interaction/commands/src/types.ts` | `CommandDefinition`, `CommandResult`, `CommandExecution`, and event types. |
| `packages/interaction/commands/src/brand.ts` | Branded command definition and execution IDs. |
| `packages/interaction/commands/README.md` | Package contract and design rationale. |

### Browser command surface

| File | Role |
| --- | --- |
| `packages/client/ui-commands/src/client/service.ts` | Slash source, catalog lookup, input claims, dispatch, and Remote execution. |
| `packages/client/ui-commands/src/client/directory.ts` | Per-session command descriptor cache. |
| `packages/client/ui-commands/src/client/resolution.ts` | Localized command spelling and first-party identity resolution. |
| `packages/client/ui-commands/src/client/presentation.ts` | Menu labels, icons, and sections. |
| `packages/client/ui-commands/README.md` | Browser-side command design. |

### Remote transport

| File | Role |
| --- | --- |
| `packages/api/remotes/src/client/index.ts` | Mounts the `commands` Remote contribution into the Client assembly. |
| `packages/api/remotes/src/index.ts` | Host-side Remote event bridge and forwarded event source. |
| `packages/api/remotes/src/remote-events.ts` | Allowlist for forwarded command registry changes. |

### Conversation rendering

| File | Role |
| --- | --- |
| `packages/client/ui-chat/src/client/conversation-nodes/command.ts` | Pairs `command/run` and `command/done` and builds the chat node. |
| `packages/client/ui-conversation/src/client/contract/records.ts` | Defines `CommandNode`. |
| `packages/client/ui-conversation/src/client/contract/chat-nodes.ts` | Chat node data map and renderer contracts. |

### Example command producers

| File | Command |
| --- | --- |
| `packages/plan/plan-mode/src/index.ts` | `/plan` |
| `packages/goal/command-goal/src/index.ts` | `/goal` |
| `packages/compaction/command-compact/src/index.ts` | `/compact` |
| `packages/feedback/command-feedback/src/index.ts` | `/feedback` |
| `packages/session-query/session-log-export/src/index.ts` | `/export` |

### CLI plugin management

| File | Role |
| --- | --- |
| `apps/cli/src/args.ts` | Parses `dsh plugin --profile <name> <pnpm args>`. |
| `apps/cli/src/bin.ts` | Dispatches the CLI invocation to `runPlugin()`. |
| `apps/cli/src/plugin.ts` | Initializes the profile, runs pnpm, and reconciles bundle layers. |

## 13. Design lessons

### 13.1 Direct commands should not masquerade as model messages

The user intentionally selected a control. Treating its reply as an assistant message would pollute model history and make a UI action look like model-generated content.

### 13.2 Durable lifecycle events are better than UI-only state

Because `command/run` and `command/done` are logged, other browser tabs and resumed sessions can reconstruct the command row. The calling browser's immediate RPC response is only an acknowledgment; it is not the sole source of truth for presentation.

### 13.3 The generic registry should not own business behavior

`CommandRuntime` knows how to parse, admit, invoke, cancel, and log. `/plan` owns plan state. `/compact` owns compaction. This keeps the command mechanism reusable.

### 13.4 Correlation IDs make partial history survivable

Pairing by `commandId` avoids relying on adjacent rows. A loaded history window may contain only the start or only the settlement, and the conversation layer can still render a useful result.

### 13.5 Admission failure and handler failure are different

An unknown command never entered the command lifecycle. A known command whose handler returns an error did enter it. The UI, session log, and retry behavior depend on preserving that distinction.

## 14. Exercises

1. Trace `/goal clear` from `ui-commands` to `commandDefinition` and identify where the goal domain event is written.
2. Find the test that verifies `command/run` and `command/done` pairing for `/compact`.
3. Add a small command with no input and determine whether it uses `runDetached()`.
4. Add a command with `input.attachments: true` and trace the mixed image/file admission order.
5. Explain why an unknown `/does-not-exist` should produce no `command/run` event.
6. Compare `/plan off` with `dsh plugin --profile web add <package>` and list every subsystem that differs.

## 15. One-page summary

```text
Plugin registration
    -> ctx.commands.register()
    -> descriptor available to UI

User slash input
    -> ui-commands resolves catalog
    -> remote.commands.execute()

Host execution
    -> parse and resolve
    -> command/run
    -> plugin handler
    -> command/done

Reply rendering
    -> session events forwarded
    -> command Definition pairs by commandId
    -> visible command node

Model boundary
    -> command text is not assistant history
    -> business plugin may separately change Agent/domain state

CLI plugin management
    -> dsh plugin
    -> pnpm in profile directory
    -> dsh.profile.bundles reconciliation
    -> next profile boot
```
