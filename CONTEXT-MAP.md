# Context Map

## Contexts

- [MobileGym Test Platform](./docs/test-platform/CONTEXT.md) — defines reusable
  benchmark workflows, exact execution identities, runs, evidence, reports, and
  strict baselines.

## Relationships

- **Test Platform → Benchmark Engine**: the Test Platform compiles a frozen run
  contract and asks `bench_env` to execute its prepared episodes.
- **Test Platform → Simulator**: the Test Platform addresses immutable target
  observations while the simulator supplies state preparation, execution, and
  result evidence.
- **Benchmark Engine → Simulator**: `bench_env` drives the simulator and returns
  episode results and artifacts without owning Test Platform product identity.
