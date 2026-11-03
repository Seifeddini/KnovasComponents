# Sync component

One deployable connection between a company's source and its Brain, under [connector protocol v1](../../docs/connector-protocol-v1.md). It runs at the company, calls out only, and takes its whole configuration from the Platform.

## A new connection

1. **In the Platform:** Builder → *Verbinden* → *Komponente anmelden* → *Sync-Komponente*. The last step shows one `docker run` line with a one-time token (24 hours).
2. **At the company:** run that line. The component makes its own key, enrols, and shows as online.
3. **In the Platform:** create the source on that component and map it onto the Brain, as for any source.
4. **Dry run, then apply:** both from the component's page. The component writes to the Brain only on a command a person gave, and an apply writes exactly the plan the dry run made. A schedule only runs dry runs.

A source's credential stays at the company: add `-e SYNC_CREDENTIAL_<KEY>=…` (the source's key in upper case). It never travels through the Platform.

## Getting the image to a company

There is no image registry yet. Build it once, then copy it:

```bash
KB_READ_TOKEN=… docker compose --profile sync-component build sync-component
docker save sync-component:0.1.0 | gzip > sync-component.tar.gz   # on the company's machine: docker load < sync-component.tar.gz
```

## A new kind of source

One function and one entry in `src/sync_component/fetchers.py`: it reads the source and writes the document the source's mappings read. Today: `http_json`. A new kind also needs its entry in the Platform's `builder/sources.py`, and a compiled mapping that reads what the function writes.

## Settings

`python -m sync_component --help` and the top of `src/sync_component/__main__.py`.

## What it does not do yet

- Documents and permissions: only records, through the mapping runner. The streams for content and permissions are in the contract as *not built*.
- Renew its certificate by itself: it holds for 365 days; *Token erneuern* in the Platform enrols it again.

## Tests

`docbridge_integration/tests/test_sync_component.py`: the real component over real HTTP against the Platform's protocol.
