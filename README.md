# 合规规则判定 API（Python / FastAPI）

基于 **FastAPI** 的合规规则判定服务：接收事实字典（`facts`）、命名规则集合（`rules`）
和目标规则名（`target`），先解析规则引用、拒绝缺失引用与循环依赖，再按
**true / false / unknown 三值逻辑**求值，并返回完整的判定追踪信息。

- 纯本地运行：**无需外部账号、密钥或付费服务**，无数据库依赖。
- 结果稳定：相同请求永远产生相同响应（所有追踪列表均排序输出）。
- 即使规则在逻辑上可短路，服务仍会访问全部可达节点，保证追踪信息完整。

---

## 快速开始

```bash
docker compose up --build
```

启动后：

| 用途 | 地址 |
| --- | --- |
| 健康检查 | <http://localhost:8000/health> |
| Swagger 文档 | <http://localhost:8000/docs> |
| OpenAPI Schema | <http://localhost:8000/openapi.json> |
| 判定接口 | `POST http://localhost:8000/evaluate` |

镜像在构建阶段已完成依赖安装与源码字节码预编译（启动初始化纳入镜像），
容器以非 root 用户运行。

---

## 请求格式

```jsonc
{
  "facts": { "age": 17, "consent": true, "user": { "country": "CN" } },
  "rules": {
    "is_adult":  { "gte": ["age", 18] },
    "minor":     { "not": { "ref": "is_adult" } },
    "cn_minor":  { "all": [ { "ref": "minor" },
                            { "eq": ["user.country", "CN"] } ] }
  },
  "target": "cn_minor"
}
```

### 规则节点

每个节点是一个对象，**恰好包含一个**操作符键：

| 节点 | 形式 | 含义 |
| --- | --- | --- |
| 全部满足 | `{"all": [节点, ...]}` | Kleene 逻辑与 |
| 任一满足 | `{"any": [节点, ...]}` | Kleene 逻辑或 |
| 取反 | `{"not": 节点}` | 逻辑非 |
| 引用 | `{"ref": "规则名"}` | 引用另一条命名规则 |
| 事实比较 | `{"eq": ["字段", 字面值]}` | 支持 `eq / ne / gt / gte / lt / lte` |

- `all` / `any` 要求非空数组；`not` 要求单个节点；`ref` 要求非空字符串。
- 比较节点固定为 `[字段路径, 字面值]`，字面值允许字符串、数字、布尔、`null`。
- 字段路径支持点号嵌套，如 `"user.country"`；任一段缺失即视为该字段未提供。
- 事实字典中的显式 `null` 是**已提供**的值（可参与 `eq`/`ne`）。

### 三值逻辑（Kleene）

| 运算 | 规则 |
| --- | --- |
| `not` | `not unknown = unknown` |
| `all` | `false` 主导；无 `false` 且有 `unknown` 时为 `unknown`；全真才为 `true` |
| `any` | `true` 主导；无 `true` 且有 `unknown` 时为 `unknown`；全假才为 `false` |

`unknown` 的来源：

1. 比较引用的事实字段**未提供**；
2. 大小比较类型不兼容（如数字与字符串比较、与布尔值比较、与 `null` 做大小比较）。

说明：`eq`/`ne` 遵循 JSON 类型语义——数字只与数字相等（`1` 与 `1.0` 相等），
布尔值不等于数字（`true ≠ 1`）；类型不同则 `eq=false`、`ne=true`。

---

## 响应格式

```json
{
  "result": "true",
  "used_facts": ["age", "user.country"],
  "missing_fields": [],
  "hit_paths": [
    "cn_minor",
    "cn_minor.all[0]",
    "cn_minor.all[0].ref",
    "cn_minor.all[1]",
    "cn_minor.all[1].eq",
    "is_adult",
    "is_adult.gte",
    "minor",
    "minor.not",
    "minor.not.ref"
  ]
}
```

| 字段 | 说明 |
| --- | --- |
| `result` | `"true"` / `"false"` / `"unknown"` |
| `used_facts` | 求值过程中**实际读取**到的事实字段（排序） |
| `missing_fields` | 规则引用但事实中**未提供**的字段（排序） |
| `hit_paths` | 求值访问到的**全部规则节点路径**（排序，短路也不省略） |

路径命名规则：

- 命名规则的根节点就是规则名，如 `is_adult`；
- 组合节点带结构位置：`r.all[0]`、`r.any[1].not`；
- 比较叶节点额外追加操作符段：`r.all[1].eq`、`is_adult.gte`；
- 引用节点额外追加 `.ref` 段，被引用规则的规则体路径也会一并列出；
- 同一规则被多次引用时，其规则体路径在 `hit_paths` 中只出现一次（集合去重），
  但每个引用位置（`.ref`）都会记录。

---

## 错误处理（HTTP 400）

求值前先做完整校验，所有错误一次性收集并按确定顺序返回。

| `error` 代码 | 触发条件 |
| --- | --- |
| `invalid_rules` | `rules` 为空或不是对象 |
| `invalid_rule_structure` | 节点结构非法（无/多个操作符、数组为空、字面值非法等），`details` 给出每个问题的 `path` 与 `message` |
| `missing_reference` | 某 `ref` 指向不存在的规则名（在循环检测之前执行） |
| `reference_cycle` | 引用图存在环，`details` 给出归一化后的环，如 `["a", "b", "a"]` |
| `unknown_target` | `target` 指定的规则不存在 |

缺失引用示例：

```json
{
  "error": "missing_reference",
  "message": "1 referenced rule(s) are not defined",
  "details": [ { "missing_rule": "ghost", "at": "r.ref" } ]
}
```

请求体本身不符合 Schema（缺字段、类型错误）时由 FastAPI 返回 `422`。

---

## 使用示例

### 事实不足 → unknown（且不短路掉追踪）

```bash
curl -s -X POST http://localhost:8000/evaluate \
  -H 'Content-Type: application/json' \
  -d '{
    "facts": {"age": 17},
    "rules": {
      "is_adult": {"gte": ["age", 18]},
      "kyc":     {"eq": ["kyc_done", true]},
      "pass":    {"all": [{"not": {"ref": "is_adult"}}, {"ref": "kyc"}]}
    },
    "target": "pass"
  }'
```

响应（`kyc_done` 未提供使结果为 `unknown`，且两个分支都出现在 `hit_paths`）：

```json
{
  "result": "unknown",
  "used_facts": ["age"],
  "missing_fields": ["kyc_done"],
  "hit_paths": ["is_adult", "is_adult.gte", "kyc", "kyc.eq",
                "pass", "pass.all[0]", "pass.all[0].not",
                "pass.all[0].not.ref", "pass.all[1]", "pass.all[1].ref"]
}
```

### 循环引用被拒绝

```bash
curl -s -X POST http://localhost:8000/evaluate \
  -H 'Content-Type: application/json' \
  -d '{"facts":{},"rules":{"a":{"ref":"b"},"b":{"ref":"a"}},"target":"a"}'
```

```json
{
  "error": "reference_cycle",
  "message": "1 reference cycle(s) detected between rules",
  "details": [ { "cycle": ["a", "b", "a"] } ]
}
```

---

## 本地开发（不使用 Docker）

需要 Python 3.11+：

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

运行测试：

```bash
pytest -q
```

## 项目结构

```
.
├── app/
│   ├── __init__.py
│   ├── engine.py      # 三值逻辑规则引擎：校验、引用解析、循环检测、求值与追踪
│   └── main.py        # FastAPI 应用：POST /evaluate、GET /health
├── tests/
│   ├── test_engine.py # 引擎单元测试
│   └── test_api.py    # HTTP 集成测试
├── requirements.txt
├── Dockerfile         # 构建期安装依赖 + 字节码预编译，非 root 运行
├── docker-compose.yml # 单条命令暴露 8000 端口
└── README.md
```

## 设计说明

- **求值顺序无关性**：`all`/`any` 始终访问全部子节点而非真正短路。由于 Kleene
  三值逻辑是单调的，全量访问得到的值与短路求值完全一致，但追踪信息更完整。
- **引用展开与记忆化**：规则值按规则名记忆化（DAG 中同一规则被引用多次只计算一次）；
  规则体路径在每次引用时都重新加入追踪，保证每个调用位置的路径都完整。
- **确定性输出**：规则名与规则遍历顺序固定，`used_facts` / `missing_fields` /
  `hit_paths` 均经排序输出，同一请求结果稳定可复现。
