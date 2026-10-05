# 更正与补充说明

本文件用于更正 `AUDIT-REPORT.md` 中一处不充分的修复建议，并记录本工作区的文件写保护行为。

---

## 一、更正：`AUDIT-REPORT.md` 中「缺陷 3」的修复建议作废

`AUDIT-REPORT.md` 第 224–231 行原建议「把 `src="${c.thumbnail}"` 改为 `src="${esc(c.thumbnail)}"`」。**该建议不充分，特此作废。**

**原因**：`esc()` 的实现是设置 `textContent` 后读取 `innerHTML`。按 HTML 片段序列化规范，文本节点只转义 `&`、`<`、`>`，**不转义引号**。因此：

| 上下文 | 写法 | `esc()` 是否足够 |
|---|---|---|
| 文本节点 | `<div>${esc(x)}</div>` | ✅ 安全 |
| **属性值** | `<img src="${esc(x)}">` | ❌ **仍可被 `"` 突破** |

**正确修复**：使用转义引号的专用函数。

```js
function escAttr(s) {
  return String(s ?? '').replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// templates/index.html 第 571 行改为：
thumbHtml = `<img src="${escAttr(c.thumbnail)}" alt="">`;
```

**另一处需要澄清的点**：单独做 `^https?://` 协议前缀校验**并不能**阻止注入——`https://x" onerror="alert(1)` 依然以 `https://` 开头。协议校验只能作为纵深防御的补充，必须与引号转义配合使用。

---

## 二、本工作区的文件写保护行为（排查时须知）

在此环境下实测到一条不直观的规则：

> **`reclip\` 目录下的文件只在「创建它的那一轮对话」内可写，进入后续轮次后即变为只读。**

具体表现（均已实测）：

| 现象 | 结果 |
|---|---|
| `attrib app.py` | 无只读位（仅 `A` 归档属性） |
| `os.chmod(app.py, S_IWRITE)` | 调用成功，但写入仍 `EACCES` |
| `cp` / `mv` / `git apply` 覆盖已存在文件 | `Permission denied` |
| `open(path, 'a')` | `[Errno 13] Permission denied` |
| Bash `>>` 追加 | `Permission denied` |
| 写入**新建**文件 | ✅ 正常 |
| `git add` / `git commit`（写 `.git`） | ✅ 正常 |

**影响**：无法原地修改跨轮次的文件（如 `app.py`、`AUDIT-REPORT.md`）。本轮因此改为「产出补丁 + 新建文件」的方式交付修改。

**绕行建议**：若需要修改既有文件，把它复制到新路径再改（副本不受限制），或在下一轮重新创建该文件。
