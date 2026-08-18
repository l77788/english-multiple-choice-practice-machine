# 便携版（绿色版）构建说明

英语刷题机提供一套面向普通用户的**绿色便携版**：把官方 Windows Embedded Python、项目依赖、前端、内置题库和启动器打成一个文件夹，压缩成一个 zip。用户**解压 → 双击 → 即可使用**，全程不需要安装 Python、不需要 Node、不需要管理员权限。

相比「打成单文件 exe」的方式，便携版**启动更快、更不容易被杀软误报、几乎没有「双击没反应 / 依赖缺失」的问题**。

---

## 一、原理：为什么这样最省心

同一个应用有三层组成：

```
后端  (Python + FastAPI + uvicorn，跑在 127.0.0.1 本地 HTTP)
前端  (Vue 构建产物 frontend/dist，由内嵌浏览器加载)
外壳  (pywebview 桌面窗口)
```

传统的「单文件 exe」要把这整套东西在**运行时**自解压到临时目录，所以启动慢、结构特殊易被杀软标记。而便携版把这三层**原样放成一个文件夹**，没有运行时解包，故启动最快、最稳。

固定分发形态：`一套可随时替换的解释器（Embedded Python）+ 依赖 + 应用文件 + 启动器`。

## 二、前置条件

构建是在**开发者**（或 CI）机器上进行的，只需要一次：

| 项 | 说明 |
| --- | --- |
| Python 3.12 / 3.13 | 用于创建 `.venv` 并安装依赖 |
| Node.js + pnpm (corepack) | 用于构建前端 `frontend/dist` |
| 网络 | 首次构建需下载官方 Embedded Python（约 11MB） |

最终分发的 zip 对用户**零依赖**。

## 三、本地构建

1. 准备好项目运行环境（与「从源码运行」相同）：
   ```powershell
   py -3.13 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   cd frontend
   corepack pnpm install --frozen-lockfile
   corepack pnpm run build
   cd ..
   ```
2. 执行构建脚本：
   ```powershell
   .\build-green-portable.ps1
   脚本会自动完成：
   1. 从 `.venv` 探测 Python 版本，下载与之匹配的官方 Embedded Python；
   2. 配置 `pythonXXX._pth` 启用 `site` 并挂载 `Lib\site-packages`；
   3. 复制依赖、后端代码、`frontend/dist`、内置题库；
   4. 生成双击启动器；打包成 zip。

3. 构建产物：
   - zip：`.build\英语刷题机-便携版.zip`
   - 组装目录：`.build\portable\`

> 提示：设为 `-EmbedPyVersion` 可指定特定的嵌入式版本（需与 `.venv` 的 Python 大版本一致，以确保编译扩展兼容）：
> ```powershell
> .\build-green-portable.ps1 -EmbedPyVersion 3.13.7
> ```

## 四、分发与使用

拿到 `英语刷题机-便携版.zip` 后：

1. **解压**到任意位置（桌面、D 盘均可）。
2. 进入解压出的 `英语刷题机-便携版` 文件夹。
3. **双击 `启动英语刷题机.vbs`**。
4. 等待桌面窗口弹出，即可开始刷题。

首次启动会自动把内置题库装入数据库（无需手动导入），之后每次启动会更快。可把 `启动英语刷题机.vbs` 发送到桌面快捷方式或固定到任务栏。

> 注意事项：
> - 请把整个文件夹当作一个应用整体，不要单独删掉里面的文件。
> - 做题记录、错题本、单词本等本地数据保存在 `backend\data`（不随 zip 分发，回传后由程序自动重建）。
> - 若窗口未弹出，查看便携目录下的 `app.log` 排查。

## 五、关键文件

| 文件 | 作用 |
| --- | --- |
| `build-green-portable.ps1` | 一键构建便携 zip（本地 / CI 均可用） |
| `tools/gen_launcher.py` | 生成中文名双击启动器（交给 Python 处理，规避编码问题） |
| `tools/zip_portable.py` | 用 UTF-8 条目名打包 zip |
| `desktop_app.py` | 桌面窗口入口（启动本地服务 + 打开窗口） |
| `启动英语刷题机.vbs` | 桌面应用的「双击即用」启动器 |

## 六、CI 自动构建（打 tag 即出发行物）

仓库已接入 GitHub Actions 工作流 `.github/workflows/build.yml`。**只要推一个 `v*` 标签**（或在 Actions 页面手动触发 `workflow_dispatch`），CI 就会在 Windows runner 上自动完成全套构建并产出发行物：

1. 安装 Python 3.13，创建 `.venv` 并安装依赖（含 PyInstaller）；
2. 安装 Node + pnpm，构建前端 `frontend/dist`；
3. 用 PyInstaller 打出单文件 exe（`dist\英语刷题机.exe`）；
4. 执行 `.\build-green-portable.ps1` 打出绿色便携 zip（`.build\英语刷题机-便携版.zip`）；
5. 两者都上传为 Actions artifact，并在打 tag 时通过 `softprops/action-gh-release` 挂到对应的 GitHub Release 上。

CI 与本地构建的关键区别：CI 里用 `setup-python` 创建 `.venv`，脚本自动据此探测匹配的 Embedded Python 版本下载，无需手动指定。

```powershell
git tag v1.1.0
git push origin v1.1.0
# Release 页会自动出现 英语刷题机-便携版.zip 与 英语刷题机.exe
```