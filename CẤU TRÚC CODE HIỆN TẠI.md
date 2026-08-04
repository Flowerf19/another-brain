# Cấu trúc code hiện tại

Tài liệu này mô tả cấu trúc đang được thực thi của Another Brain `v0.11.1`.
Runtime hiện tại là một Python package native dùng chung cho Windows và Ubuntu,
với SQLite/FTS5, NumPy và ONNX Runtime CPU.

## Cây thư mục chính

```text
another-brain/
├── src/
│   └── another_brain/
│       ├── __init__.py
│       ├── app.py
│       ├── cli.py
│       ├── config.py
│       ├── errors.py
│       ├── service.py
│       ├── domain/
│       │   ├── __init__.py
│       │   ├── models.py
│       │   └── retention.py
│       ├── embedding/
│       │   ├── __init__.py
│       │   ├── installer.py
│       │   ├── manifest.py
│       │   ├── payload.py
│       │   └── provider.py
│       ├── storage/
│       │   ├── __init__.py
│       │   ├── connection.py
│       │   ├── repository.py
│       │   └── schema.py
│       ├── retrieval/
│       │   ├── __init__.py
│       │   ├── fusion.py
│       │   └── service.py
│       └── mcp/
│           ├── __init__.py
│           └── server.py
├── tests/
│   └── native/
│       ├── conftest.py
│       ├── test_cli_complete.py
│       ├── test_config.py
│       ├── test_config_complete.py
│       ├── test_domain_complete.py
│       ├── test_embedding_complete.py
│       ├── test_mcp.py
│       ├── test_mcp_complete.py
│       ├── test_retrieval.py
│       ├── test_retrieval_complete.py
│       ├── test_service.py
│       ├── test_service_complete.py
│       ├── test_storage.py
│       ├── test_storage_complete.py
│       └── test_transport_complete.py
├── scripts/
│   ├── install.ps1
│   ├── install.sh
│   ├── connect.ps1
│   └── connect.sh
├── docs/
│   ├── architecture.md
│   ├── deployment.md
│   ├── mcp-tools.md
│   └── memory-trust-model.md
├── skills/
│   └── another-brain/
│       └── SKILL.md
├── .github/
│   └── workflows/
│       └── unit-tests.yml
├── pyproject.toml
├── uv.lock
├── README.md
└── LICENSE
```

Các thư mục môi trường ảo, cache, model test, database test và artifact build
được bỏ qua vì không thuộc source code phát hành.

## Luồng thực thi chính

```text
MCP host hoặc người dùng CLI
            │
            ▼
      another-brain
        cli.py
            │
            ▼
         app.py
   khởi tạo các dependency
            │
            ▼
       service.py
   điều phối nghiệp vụ memory
       ┌────┼───────────┐
       ▼    ▼           ▼
   domain embedding  retrieval
              │        │
              ▼        ▼
        ONNX Runtime  storage
                       │
                       ▼
                 SQLite + FTS5
```

Luồng mặc định của MCP là stdio. HTTP Streamable chỉ được bật khi chạy
`another-brain serve --http` và chỉ cho phép địa chỉ loopback dạng số.

## Vai trò các module runtime

### Package gốc

- `__init__.py`: khai báo phiên bản package.
- `cli.py`: console entry point `another-brain`; cung cấp server, model,
  doctor, recent và admin commands.
- `config.py`: đọc biến môi trường, chọn đường dẫn native theo hệ điều hành và
  kiểm tra cấu hình HTTP loopback.
- `app.py`: composition root, khởi tạo repository, retriever, embedder và
  `MemoryService`.
- `service.py`: lớp nghiệp vụ trung tâm cho remember, search, recent, get,
  reinforce, forget, restore, hard-delete, audit và health.
- `errors.py`: nhóm exception công khai của ứng dụng.

### `domain/`

- `models.py`: định nghĩa `MemoryRecord`, `MemoryScope`, `SearchFilters`,
  `SearchResult` và validation cấp domain.
- `retention.py`: ánh xạ importance `1..5` sang TTL
  `7/30/90/180/365` ngày.

### `embedding/`

- `manifest.py`: pin repository, revision, dimension 640, query prompt và
  SHA-256 của model Harrier q4.
- `installer.py`: tải model vào staging, kiểm tra hash và publish model.
- `payload.py`: tạo document payload từ topic + summary và query payload có
  prompt riêng.
- `provider.py`: tokenize và chạy model bằng ONNX Runtime CPU; kiểm tra token
  budget và vector output.

### `storage/`

- `schema.py`: SQLite schema version 1, các bảng memory/audit/profile, index,
  FTS5 và trigger đồng bộ.
- `connection.py`: bootstrap database, schema lock, migration checksum và các
  SQLite pragma như WAL, foreign keys, busy timeout, page size 16 KiB.
- `repository.py`: lưu, đọc, lifecycle, audit, FTS5 candidates và vector rows.

SQLite là source of truth duy nhất. Mỗi hệ điều hành sử dụng database trong
user-data directory native của chính hệ điều hành đó.

### `retrieval/`

- `service.py`: chạy độc lập hai nhánh FTS5 và exact cosine bằng NumPy; cosine
  floor `0.30` chỉ áp dụng cho vector candidates.
- `fusion.py`: hợp nhất thứ hạng lexical/vector bằng reciprocal-rank fusion,
  `k=60`, tối đa 5 kết quả.

### `mcp/`

- `server.py`: tạo MCP SDK v2 server và công bố tám tools:
  `brain_remember`, `brain_search`, `brain_recent`, `brain_get`,
  `brain_reinforce`, `brain_forget`, `brain_health`, `brain_audit`.

## Cấu trúc dữ liệu chính

Một memory gồm các nhóm dữ liệu:

- Identity: `memory_id`, `brain_id`, `agent_id`.
- Phạm vi: `scope`, `scope_id`.
- Nội dung: `topic`, `catalog`, `summary`, `content`, `metadata`.
- Timeline: `timeline_day`, `period_start`, `period_end`, `created_at`,
  `updated_at`.
- Retention: `importance`, `expires_at`, `deleted_at`.
- Embedding: profile q4 input version 2 và FLOAT32 vector 640 chiều.

Topic đã humanize cùng summary được dùng để tạo embedding. Content chỉ tham
gia FTS5 và không được đưa vào embedding.

## Bộ test hiện tại

### Fixtures

- `conftest.py`: cung cấp config, SQLite repository, fake embedder và
  `MemoryService` dùng chung.

### Các lớp test

- `test_domain_complete.py`: validation domain, scope, TTL, metadata, vector.
- `test_config*.py`: native paths, timezone, port và environment validation.
- `test_embedding_complete.py`: payload, manifest, installer, hash, provider,
  token budget và optional real ONNX gate.
- `test_storage*.py`: schema, pragma, FTS5, persistence, lifecycle, audit,
  isolation và concurrent writers.
- `test_retrieval*.py`: lexical/vector search, cosine floor, tiếng Việt, RRF,
  filtering và giới hạn kết quả.
- `test_service*.py`: nghiệp vụ remember/search/get và toàn bộ lifecycle.
- `test_mcp*.py`: tool list, schema, đủ tám tool calls, error response và
  response privacy.
- `test_cli_complete.py`: version, model, doctor, recent, admin và transport
  dispatch.
- `test_transport_complete.py`: subprocess MCP stdio và HTTP loopback thật.

Suite chuẩn hiện có kết quả:

```text
113 passed
15 xfailed
1 skipped
branch coverage: 91.31%
```

Các `xfail(strict=True)` ghi lại những hợp đồng chưa được runtime đáp ứng.
Slow test được skip nếu chưa khai báo thư mục model ONNX thật.

## Entry point và lệnh vận hành

Console entry point trong `pyproject.toml`:

```text
another-brain = another_brain.cli:main
```

Các lệnh chính:

```text
another-brain
another-brain serve --http
another-brain doctor
another-brain model status
another-brain model pull
another-brain recent --scope global
another-brain admin restore <memory-id>
another-brain admin hard-delete <memory-id>
```

## Cài đặt native

Windows:

```powershell
.\scripts\install.ps1
.\scripts\connect.ps1 codex
```

Ubuntu:

```bash
./scripts/install.sh
./scripts/connect.sh codex
```

Hai hệ điều hành sử dụng cùng package và lockfile, nhưng database/model cache
nằm trong các thư mục native riêng nên không ghi đè lẫn nhau.

## Chạy test

Suite chuẩn và coverage gate:

```text
uv sync --locked
uv run pytest --cov=another_brain --cov-branch --cov-report=term-missing --cov-fail-under=90
```

Gate model ONNX thật trên Windows:

```powershell
$env:ANOTHER_BRAIN_TEST_MODEL_DIR = "C:\path\to\model"
uv run pytest -m slow
```

Gate model ONNX thật trên Ubuntu:

```bash
ANOTHER_BRAIN_TEST_MODEL_DIR=/path/to/model uv run pytest -m slow
```
