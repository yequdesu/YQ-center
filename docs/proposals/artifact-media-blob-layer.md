# Artifact / Media / Blob Layer Proposal

状态：proposal
日期：2026-06-28
目标：为文件传输、截图、摄像头、多模态 Agent、日志包、审计附件等能力设计统一基础层。

## 1. 背景

当前 Center 已具备 Node、Runtime、Capability、Job、Approval、Timeline 和 Agent 编排能力。下一阶段如果扩展 Win node 截图、摄像头、Linux node 文件能力、跨设备文件传输、多模态 Agent 输入输出，会反复遇到同一个问题：

Job result 和 Timeline event 适合存结构化 JSON，不适合承载二进制内容。

如果把文件、图片、音频、视频直接 base64 塞进 `job.finished.output`，会带来：

- 数据库膨胀。
- SSE / API 响应过大。
- 前端预览困难。
- 无法做权限授权和过期清理。
- 无法跨 Node 安全中转。
- 无法给 Agent provider 做统一多模态输入适配。

因此需要一个通用的 Artifact / Media / Blob 基础层，而不是只做狭义“文件传输”。

## 2. 目标

该层应统一支持：

- 设备间文件传输。
- 截图、摄像头照片、录屏。
- 音频采集。
- 命令输出附件和日志包。
- Maintenance run artifacts。
- Timeline 附件。
- Approval 前预览附件。
- Agent 多模态输入。
- Agent 多模态输出。
- 跨 Node 临时中转缓存。

核心抽象：

```text
Artifact Store
  = Blob Storage
  + Metadata Registry
  + Access Grants
  + Provenance
  + Lifecycle
```

## 3. 非目标

第一版不做：

- 分布式对象存储集群。
- P2P Node 直连传输。
- 复杂内容搜索。
- 大规模媒体转码。
- 长期相册/网盘产品形态。
- Agent 自动读取任意用户文件。

第一版只做 Center 中转和可审计的 artifact 引用。

## 4. 核心模型

### 4.1 Artifact

建议新增 `artifacts` 表：

| 字段 | 说明 |
|---|---|
| `artifact_id` | 外部稳定 ID。 |
| `kind` | `file`、`image`、`video`、`audio`、`log`、`archive`、`json`、`blob`。 |
| `mime_type` | MIME 类型。 |
| `filename` | 原始或建议文件名。 |
| `size_bytes` | 字节数。 |
| `sha256` | 内容哈希。 |
| `storage_backend` | `local`、未来可为 `s3`、`minio`、`r2`。 |
| `storage_key` | 后端存储键。 |
| `source_node_id` | 来源 Node。 |
| `job_id` | 关联 Job，可为空。 |
| `invocation_id` | 关联 Invocation，可为空。 |
| `session_id` | 关联 Agent session，可为空。 |
| `approval_id` | 关联 Approval，可为空。 |
| `timeline_event_id` | 关联 Timeline event，可为空。 |
| `created_by_type` | `node`、`user`、`agent`、`system`。 |
| `created_by_id` | 创建者 ID。 |
| `created_at` | 创建时间。 |
| `expires_at` | 过期时间。 |
| `status` | `available`、`expired`、`deleted`、`quarantined`。 |
| `metadata_json` | 扩展元数据。 |

### 4.2 Artifact Grant

建议新增 `artifact_grants` 表：

| 字段 | 说明 |
|---|---|
| `grant_id` | 授权 ID。 |
| `artifact_id` | Artifact ID。 |
| `grantee_type` | `node`、`user`、`agent`、`system`。 |
| `grantee_id` | 被授权方 ID。 |
| `permission` | `read`、`write`、`delete`。 |
| `expires_at` | 授权过期时间。 |
| `max_uses` | 最大使用次数。 |
| `used_count` | 已使用次数。 |
| `created_at` | 创建时间。 |

## 5. 存储后端

第一版使用本地文件系统：

```text
data/artifacts/
  sha256-prefix/
    artifact_id.blob
```

要求：

- 写入时计算 sha256。
- 元数据中的 `size_bytes` 必须与实际文件一致。
- 下载前校验 artifact 状态和 grant。
- 删除只更新状态，物理清理交给后台任务。

后续可替换为 S3 / MinIO / R2，保持 `storage_backend` 和 `storage_key` 抽象稳定。

## 6. API 设想

### 6.1 Node Artifact API

Node 上传：

```http
POST /yqp/artifacts
Authorization: Bearer <node_token>
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 说明 |
|---|---|
| `file` | 二进制内容。 |
| `kind` | artifact kind。 |
| `job_id` | 可选，关联 Job。 |
| `invocation_id` | 可选。 |
| `metadata` | JSON 字符串。 |

Node 下载：

```http
GET /yqp/artifacts/{artifact_id}
Authorization: Bearer <node_token>
```

要求：

- Node 只能下载授权给自己的 artifact。
- 下载会增加 grant `used_count`。
- 未授权返回 403。

### 6.2 Admin / Console API

```http
GET  /admin/artifacts
GET  /admin/artifacts/{artifact_id}
GET  /admin/artifacts/{artifact_id}/download
POST /admin/artifacts/{artifact_id}/grants
DELETE /admin/artifacts/{artifact_id}
```

Console 第一版只需要：

- 查看 artifact metadata。
- 图片预览。
- 下载按钮。
- 在 Job / Timeline / Approval / Agent turn 中显示 artifact 附件。

## 7. 与 Job 的关系

Job result 不存二进制，只存 artifact 引用。

示例：

```json
{
  "artifacts": [
    {
      "artifact_id": "art_01H...",
      "kind": "image",
      "mime_type": "image/png",
      "filename": "screenshot.png",
      "size_bytes": 245760,
      "sha256": "..."
    }
  ]
}
```

Node 执行截图：

```text
desktop.screenshot.capture
  -> Node capture image
  -> Node upload artifact
  -> Node job.finished output contains artifact reference
```

## 8. 与文件传输的关系

跨设备文件传输使用 Center 中转：

```text
source node
  -> file.export uploads artifact
  -> Center grants read to target node
target node
  -> file.import downloads artifact
  -> writes target path
```

第一版 capability：

| capability | effect | 说明 |
|---|---|---|
| `file.export` | `read` | 从源 Node 白名单路径读取文件并上传 artifact。 |
| `file.import` | `write` | 目标 Node 下载 artifact 并写入白名单路径。 |

`file.import` 必须走审批，除非 execution mode 明确允许该风险级别。

## 9. 与多模态 Agent 的关系

Agent 不直接吞大文件。Provider adapter 通过 artifact metadata 决定如何传给模型：

```text
artifact_id
  -> ArtifactService resolve
  -> provider adapter loads local bytes or signed URL
  -> sends image/audio/file input to model
```

Agent prompt_context 可以只展示 artifact metadata，不把二进制内容注入上下文。

## 10. 安全原则

1. 默认临时，显式保留才长期保存。
2. 跨 Node 访问必须通过 grant。
3. 所有 artifact 必须记录来源和关联对象。
4. 所有 artifact 必须有 sha256。
5. Job / Timeline / AgentTurn 只保存 artifact id，不保存二进制。
6. 写入目标 Node 文件系统必须经过 capability policy 和 approval。
7. Node 不得下载未授权 artifact。
8. Artifact API 不应绕过 Node token / Admin token 认证。

## 11. 第一版待办

### P0：数据模型

- 新增 `Artifact` model。
- 新增 `ArtifactGrant` model。
- 新增 Alembic migration。
- 新增本地文件系统 storage backend。

### P0：服务层

- `ArtifactService.create_upload()`
- `ArtifactService.open_download()`
- `ArtifactService.create_grant()`
- `ArtifactService.attach_to_job()`
- `ArtifactService.expire_artifact()`

### P0：Node API

- `POST /yqp/artifacts`
- `GET /yqp/artifacts/{artifact_id}`
- Node token 认证与 grant 校验。

### P1：Admin / Console API

- `GET /admin/artifacts`
- `GET /admin/artifacts/{artifact_id}`
- `GET /admin/artifacts/{artifact_id}/download`
- `POST /admin/artifacts/{artifact_id}/grants`

### P1：Job 集成

- 允许 Job output 记录 artifact references。
- Job detail / invocation detail 返回 artifact summary。
- Timeline event 可关联 artifact。

### P1：前端

- Job detail 展示 artifact。
- Timeline 展示 artifact。
- 图片 artifact 预览。
- 下载按钮。

### P2：能力接入

- `desktop.screenshot.capture`
- `camera.capture`
- `file.export`
- `file.import`
- `log.bundle.export`

### P2：生命周期

- artifact 过期扫描。
- 物理文件清理。
- grant 使用次数限制。
- 大小限制和上传超时配置。

## 12. 开放问题

- 第一版 artifact 最大大小限制是多少，建议从 100MB 开始。
- 是否需要 content-addressed 去重。
- Admin 下载是否需要单独审计事件。
- 图片预览是否需要 thumbnail。
- 多模态 provider adapter 是否读取本地文件，还是使用临时 signed URL。
- 是否需要病毒扫描或 quarantine 状态。
