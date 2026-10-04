# Solution

Design decisions and trade-offs for the orders-and-stock assessment. Each
decision is recorded here when it is made. Choices that are still open are
listed in [AGENTS.md](AGENTS.md).

## Decisions

### Web framework: FastAPI

- **Rationale:** FastAPI integrates Pydantic request and response models
  with validation and generated OpenAPI documentation. These features
  directly support clear, documented interfaces that other teams can
  build on, with limited additional integration work.

- **Alternatives:** Flask is a viable lightweight option, but equivalent
  schema validation and OpenAPI support would require extensions or
  additional implementation. Django offers useful integrated database
  tooling and application conventions, although its admin functionality
  is outside the brief. FastAPI was selected for its API-focused features
  and flexibility in choosing persistence and worker tooling.

- **Trade-offs:** Database access, migrations and durable background
  processing must be selected and integrated separately. This provides
  flexibility but leaves more architectural decisions to the application.

- **Open decisions:** Synchronous versus asynchronous database access
  and the worker arrangement will be decided separately.
