Plan compilation rejects `output_contains` and `output_regex` on terminal
operations that send text, key, or control input, including through a
continuation. Command-group output assertions follow the same rule. Output
assertions remain available with `wait_for` and `pause` input, and exit-code
and file assertions remain available with authored terminal input.
