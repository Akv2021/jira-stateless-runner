"""httpx-based Jira REST client with tenacity retry wrapper.

Per docs/ExternalRunner.md §5.1 and §6.2:

- 429 responses: respect ``Retry-After`` header.
- 5xx responses: exponential back-off, 3 retries.
- 401 responses: fail fast (no retry); signal dead-man's-switch.

Single chokepoint for all Jira REST calls.

Implementation lands in M3.
"""
