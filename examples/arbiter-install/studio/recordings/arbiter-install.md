# Install Arbiter Server with Docker

```yaml studio-directive
scene: Install Arbiter Server
```

```yaml studio-directive
recording:
  vars:
    loopback_host: 127.0.0.1
    staging_port: 18075
    installed_port: 8075
    staging_url: "https://${.loopback_host}:${.staging_port}"
    installed_url: "https://${.loopback_host}:${.installed_port}"
    reploy_app: "reploy app"
  id: install-and-bootstrap
  title: Install Arbiter Server
  capture:
    window_size: 100x28
    headless: true
    baseline_compressed: true
  failure_summary:
    terminal_animations:
    - regex: '^(?P<prefix>\[STAGING : arbiter\] building installation bundle) [|/\\-]$'
      replacement: "{prefix}..."
  requirements:
    commands:
    - docker
    - fakeroot
    - jq
  style:
    color: true
    typing: true
    typing_min_delay: 0.025
    typing_max_delay: 0.095
    typing_space_delay: 0.04
    typing_punctuation_delay: 0.08
    typing_newline_delay: 0.22
    typing_seed: 17
  outputs:
    cast: website/static/casts/install-and-bootstrap.cast
    audio: website/static/audio/casts/install-and-bootstrap.mp3
  publish:
    default: docusaurus
    surfaces:
      docusaurus:
        type: docusaurus_mdx
        file: website/docs/media/terminal-recordings.mdx
        placeholder: install-and-bootstrap
        component: TerminalCast
        intro_segment: overview
      standalone_html:
        type: standalone_html
        file: website/static/casts/install-and-bootstrap.html
        intro_segment: overview
  retime:
    typing_char_delay: 0.035
    typing_space_delay: 0.02
    typing_punctuation_delay: 0.05
    typing_newline_delay: 0.0
    post_enter_pause: 0.35
    post_command_pause: 0.85
    minimum_section_spacing: 1.0
  environment:
    working_directory: .
    variables:
      ARBITER_COLOR: always
      ARBITER_CINEMA_STAGING_SUBNET: 10.213.240.0/24
      ARBITER_CINEMA_STAGING_URL: ${recording.vars.staging_url}
      ARBITER_CINEMA_INSTALLED_URL: ${recording.vars.installed_url}
      REPLOY_COLOR: always
  audio:
    enabled: true
    provider: openai
    env: OPENAI_ARBITER_CINEMA_AUDIO_API_KEY
    model: gpt-4o-mini-tts
    voice: marin
    format: mp3
    instructions: Speak clearly and calmly, like a concise technical walkthrough.
    cache_dir: studio/cache/audio
    transcription:
      model: whisper-1
      timestamp_granularities:
      - word
      - segment
  parameters:
    arbiter_source:
      default: local
    arbiter_package:
      default: arbiter-suite
    reploy_source:
      default: local
    reploy_venv:
      default: ../reploy/.venv
    operator_venv_cache_retain:
      default: 8
  setup:
  - name: Prepare operator commands and local mail lab
    expect:
      file_exists:
      - $MAIL_LAB_ENV_FILE
    run_file: studio/recordings/install-and-bootstrap/setup-main.sh
  cleanup:
  - name: Stop Docker staging deployment
    run: |
      if [[ -f ./arbiterctl && -x ./arbiterctl ]]; then
        COMPOSE_PROGRESS=quiet ./arbiterctl down 2> >(recording_filter_docker_compose_progress >&2)
      elif [[ -f reploy-staging/arbiterctl && -x reploy-staging/arbiterctl ]]; then
        (cd reploy-staging && COMPOSE_PROGRESS=quiet ./arbiterctl down 2> >(recording_filter_docker_compose_progress >&2))
      fi
```


Purpose: show a new operator how to install Arbiter with Reploy, configure one
local bot mail account, prove the staged server works, and install the checked
staging instance into a permanent host location.

Audience: an operator preparing an Arbiter server for the first time.

Target length: 3 to 4 minutes.

Maintenance note: review this script whenever release media is refreshed. If
Reploy commands, default ports, generated files, install behavior, account
templates, or the first-run flow changes, update the script before regenerating
casts, narration, captions, or static renders.

Recording notes:

- Keep the visible operator flow focused on Reploy and Arbiter commands. Any
  helper virtual environment or local package build is recorder setup, not part
  of the tutorial.
- The recording uses a disposable `reploy-staging/` directory, a local SMTP/IMAP
  mail lab, generated test credentials, staging port `18075`, and installed
  service port `8075`.
- Do not use real mail credentials. The local mail lab and Docker staging
  deployment are cleaned up by hidden setup/cleanup directives.

## Script

### Overview

Set the tutorial frame before terminal commands begin.

```yaml studio-directive
beat:
  id: overview
  heading: Overview
  narration: >-
    In this tutorial, we will install Arbiter using the standard workflow: stage it locally with Reploy, configure and test it, then install it as a permanent Docker service on the system.
  viewer_hold: 1.0
```

### Install Reploy

Install only the deployment tool first, before any Arbiter-specific workflow.

```yaml studio-directive
beat:
  id: install-reploy
  heading: Install Reploy
  narration: >-
    First, @install@ install Reploy.
  marker: install-reploy
  caption: Install Reploy with the script installer.
  actions:
  - commands:
    - run: recording_install_reploy
      after: "@install@"
      display: "curl -fsSL https://reploy.yadan.net/install.sh | sh"
      follow_along: true
      output: suppress
  viewer_hold: 1.0
  guide:
    commands:
    - "curl -fsSL https://reploy.yadan.net/install.sh | sh"
    success_hint: The installer should report that Reploy is ready.
```

### Bootstrap Docker Staging Directory

Create the operator-owned staging workspace where the deployment is prepared
and tested.

```yaml studio-directive
beat:
  id: init-staging
  heading: Bootstrap Docker Staging Directory
  narration: >-
    Use Reploy to @create@ create the staging directory. @enter@ Then
    enter it; the rest of the setup runs from inside that staged deployment.
  marker: init-staging
  caption: Create a Docker staging deployment.
  actions:
  - commands:
    - run: |
        if [[ "$arbiter_source" == local ]]; then
          reploy stage "file:$recording_repo/server/src/arbiter_server/reploy/arbiter.blueprint.yaml"
        else
          reploy stage arbiter-server
        fi
      display: reploy stage arbiter-server
      after: "@create@"
      post_enter_pause: 1.5
    - run: cd reploy-staging
      after: "@enter@"
    expect:
      file_exists:
      - ./arbiterctl
      - ./.reploy/runtime/compose.yaml
      - ./.reploy/docker.env
  viewer_hold: 1.0
  guide:
    commands:
    - reploy stage arbiter-server
    - cd reploy-staging
    - reploy bundle list
    success_hint: You should enter the staging directory and see the selected runtime bundle.
```

### Select Mail Bundle

Choose the IMAP and SMTP runtime pieces used by the mail demo.

```yaml studio-directive
beat:
  id: prepare-bundle
  heading: Select Mail Bundle
  narration: >-
    Arbiter has a pluggable architecture. For this demo, we add the IMAP and
    SMTP plugins. @list@ First, list the available bundle options.
    @wait:list-options@ @add@ Then add the mail plugins. @build@ Now build the
    bundle. Reploy builds a local bundle from the selected dependencies, and
    that bundle becomes part of the staged app. Installation will use the tested
    artifact instead of resolving packages again later. Reploy rebuilds the
    bundle automatically when dependencies change; running the build here pays
    that cost now instead of during the next app command.
  marker: prepare-bundle
  caption: Select the mail plugin bundle options.
  actions:
  - commands:
    - id: list-options
      run: reploy bundle list-options
      after: "@list@"
    - run: reploy bundle add --name imap,smtp
      after: "@add@"
      post_enter_pause: 0.8
    - run: reploy bundle build
      after: "@build@"
      retime: realtime
      post_command_pause: 0.0
    - run: clear
      pre_command_pause: 2.0
      show_prompt_after: false
    expect:
      file_exists:
      - ./.reploy/requirements.txt
  viewer_hold: 1.0
  guide:
    commands:
    - reploy bundle list-options
    - reploy bundle add --name imap,smtp
    - reploy bundle build
    - reploy bundle list
    success_hint: You should still be inside reploy-staging and see the selected
      bundle roots.
```

### Bootstrap Arbiter Config

Generate the initial Hydra/OmegaConf config tree that the operator will edit.

```yaml studio-directive
beat:
  id: bootstrap-config
  heading: Bootstrap Arbiter Config
  narration: >-
    In this section, we will configure Arbiter to use the local test SMTP and
    IMAP servers. We will create one account named mail-demo in each plugin,
    with broad access so the demo can focus on installation and smoke testing,
    not on every policy option exposed by the mail plugins. @server@ Start by
    creating the server config. @wait:bootstrap-server@ @server_show@ Take a
    quick look at arbiter-server.yaml. This file is the Hydra entry point:
    it selects the server schema, plugin list, and the env file used at
    startup. @smtp_account@ Next, bootstrap the SMTP account and policy.
    SMTP's generated policy is broad by default: empty recipient allow and
    block lists mean Arbiter is not restricting recipients yet. @smtp_show@
    The account file points at the SMTP host and credentials. @smtp_policy_show@
    The policy keeps rate limits visible, leaves recipient restrictions open,
    and warns instead of failing if the matching IMAP Sent copy cannot be
    written. @smtp_edit@ For the recording, we edit these files to use the
    local test SMTP server. @smtp_activate@ Activate the SMTP account in the
    composed config. @smtp_env@ Then bootstrap the Arbiter env file. @smtp_secret@
    Open .arbiter.env and enter the SMTP username and password from the test
    mail lab. @imap_account@ To test mail sending end to end, add the matching
    IMAP account too. The IMAP bootstrap uses the default-open variant, which
    starts with broad folder access and lets you add deny rules later.
    @imap_show@ Inspect the generated IMAP account. @imap_policy_show@ The IMAP
    policy allows read and search broadly, and this demo edits it to allow
    writes as well. @imap_edit@ Edit the IMAP files for the local test server.
    @imap_activate@ Activate the IMAP account. @imap_env@ Re-run env bootstrap;
    it is incremental, so it adds the new IMAP variables without throwing away
    the SMTP values. @imap_secret@ Open .arbiter.env again and add the IMAP
    username and password. @check@ Now run a normal config check. It validates
    the composed YAML and schemas. @live_check@ Finally, run a live config
    check; this also logs into the test IMAP and SMTP servers.
  marker: bootstrap-config
  caption: Bootstrap editable Arbiter config files.
  actions:
  - commands:
    - id: bootstrap-server
      run: reploy app bootstrap server
      after: "@server@"
    - run: recording_show_yaml conf/arbiter-server.yaml 1 80
      after: "@server_show@"
    - run: reploy app bootstrap --plugin smtp --account mail-demo
      after: "@smtp_account@"
    - run: recording_show_yaml conf/arbiter/account/smtp/mail-demo.yaml 1 80
      after: "@smtp_show@"
    - run: recording_show_yaml conf/arbiter/policy/smtp/mail-demo_policy.yaml 1 80
      after: "@smtp_policy_show@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins smtp
      display: "$EDITOR conf/arbiter/account/smtp/mail-demo.yaml conf/arbiter/policy/smtp/mail-demo_policy.yaml"
      after: "@smtp_edit@"
    - run: reploy app config activate --plugin smtp --account mail-demo
      after: "@smtp_activate@"
    - run: reploy app env bootstrap
      after: "@smtp_env@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins smtp --update-env
      display: "$EDITOR .arbiter.env"
      after: "@smtp_secret@"
    - run: reploy app bootstrap --plugin imap --account mail-demo --variant default-open
      after: "@imap_account@"
    - run: recording_show_yaml conf/arbiter/account/imap/mail-demo.yaml 1 90
      after: "@imap_show@"
    - run: recording_show_yaml conf/arbiter/policy/imap/mail-demo_policy.yaml 1 120
      after: "@imap_policy_show@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins imap
      display: "$EDITOR conf/arbiter/account/imap/mail-demo.yaml conf/arbiter/policy/imap/mail-demo_policy.yaml"
      after: "@imap_edit@"
    - run: reploy app config activate --plugin imap --account mail-demo
      after: "@imap_activate@"
    - run: reploy app env bootstrap
      after: "@imap_env@"
    - run: recording_apply_mail_lab_config --account mail-demo --plugins imap --update-env
      display: "$EDITOR .arbiter.env"
      after: "@imap_secret@"
    - run: reploy app config check
      after: "@check@"
    - run: reploy app config check --live
      after: "@live_check@"
      retime: realtime
      post_command_pause: 0.0
    expect:
      file_exists:
      - ./conf/arbiter-server.yaml
      - ./conf/plugins.yaml
      - ./.arbiter.env
      - ./conf/arbiter/account/imap/mail-demo.yaml
      - ./conf/arbiter/policy/imap/mail-demo_policy.yaml
      - ./conf/arbiter/account/smtp/mail-demo.yaml
      - ./conf/arbiter/policy/smtp/mail-demo_policy.yaml
  viewer_hold: 1.0
  guide:
    commands:
    - reploy app bootstrap server
    - reploy app bootstrap --plugin smtp --account mail-demo
    - reploy app config activate --plugin smtp --account mail-demo
    - reploy app env bootstrap
    - reploy app bootstrap --plugin imap --account mail-demo --variant default-open
    - reploy app config activate --plugin imap --account mail-demo
    - reploy app env bootstrap
    - reploy app config check
    - reploy app config check --live
    success_hint: The normal and live config checks should both pass.
```
