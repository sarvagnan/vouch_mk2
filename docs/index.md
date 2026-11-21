# Vouch documentation

## Using Vouch

| If you want to… | Read |
|---|---|
| Walk through a scratch project from initialization to release | [Quickstart](quickstart.md) |
| Understand units, packs, evidence, deviations, releases, and tiers | [Concepts](concepts.md) |
| Look up a command or its options | [Command reference](commands.md) |
| Look up a lifecycle transition or gate | [Lifecycle reference](lifecycle.md) |
| Interpret an exit status or recover from a refusal | [Errors and recovery](errors.md) |
| Read CLI JSON, effect declarations, or MCP payloads | [JSON and MCP reference](schemas.md) |
| Find out what a report proves | [Claim boundary](claim-boundary.md) |
| Understand which threats Vouch covers | [Threat model](threat-model.md) |

## Working as an agent

Start with the [Vouch skill](agents/SKILL.md). Use the role card for the work you have been assigned.

- [Acceptance owner](agents/roles/owner.md)
- [Implementer](agents/roles/implementer.md)
- [Reviewer](agents/roles/reviewer.md)

## Changing mk2

- [Contributing and package layout](contributing.md)
- [Working method](method.md)
- [Disposition of Vouch 0.6 defects](defect-closure.md)
- [Self-hosting decision note](self-hosting-tradeoff.md)
- [Performance measurements](performance.md)
- [Subagent usage log](usage-log.md)

## Adoption design records

- [Separate the adoption module and record](adr/0001-separate-adoption-module-and-record.md)
- [Repository-local operation and no telemetry](adr/0002-repository-local-no-telemetry.md)
- [Greenfield and brownfield adoption](adr/0003-greenfield-and-brownfield-adoption.md)
- [Sound, fail-closed incremental verification](adr/0004-sound-fail-closed-incremental-verification.md)
- [Target system design](design/vouch-optimal-system.md)
