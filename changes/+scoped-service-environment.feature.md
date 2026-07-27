Resolve the OpenAI narration credential from a private project file or an
explicit CI environment value without mutating the process environment. Allow
trusted nested OmegaFlow builds to delegate that credential to one terminal
command with `with_env`, while redacting leaks and disabling capture reuse.
