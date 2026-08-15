# Warn-only editor hook fragments

These are user-wired fragments, not an installed adapter. They only print the
current Vouch verdict; they never mutate a store and never block an edit or a
commit. CI remains the hard enforcement layer.

## Claude Code `PostToolUse`

Claude Code sends hook input as JSON on stdin. For `Edit` and `Write`, the
edited path is `tool_input.file_path`, which `vouch-mk2 hook` reads directly
when `--edit-target` is omitted:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "vouch-mk2 hook"
          }
        ]
      }
    ]
  }
}
```

For clients that expose the path as an environment variable, the equivalent
one-liner is:

```sh
FILE="$CLAUDE_FILE_PATH"; vouch-mk2 hook --edit-target "$FILE"
```

## Generic pre-commit-style fragment

Wire this into the hook manager you already use. Every invocation is advisory
and exits zero, including a failed Vouch verdict or a malformed/missing store.

```sh
git diff --cached --name-only --diff-filter=ACMR -z |
  while IFS= read -r -d '' FILE; do
    vouch-mk2 hook --edit-target "$FILE"
  done
exit 0
```

