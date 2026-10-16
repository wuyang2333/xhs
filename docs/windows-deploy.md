# Windows 部署指南

## 前置条件（两种方式都要）

1. **装 Python 3.12**：https://www.python.org/downloads/release/python-3120/
   - 安装时**勾选 Add to PATH**
2. **装 Git**：https://git-scm.com/download/win
   - 一路下一步即可

---

## 方式一：全新部署（不带现有数据）

新机器上从零开始，注册新账号，空数据库。

### 步骤

1. 打开命令行（Win+R 输入 cmd）

2. 下载并运行一键安装脚本：
```bash
cd C:\
curl -o install.bat https://raw.githubusercontent.com/wuyang2333/xhs/main/install.bat && install.bat
```

3. 装完后双击 `C:\xhs\XHS.bat`，选择后台启动

4. 浏览器打开 http://localhost:5173，注册新账号

### 完成后

- 空数据库，需要重新配置模型、绑定账号、建素材库
- 以后更新：双击 `C:\xhs\update.bat`

---

## 方式二：带现有数据部署

把现有机器上的账号、素材、草稿、指令一并搬过来。

### 步骤

1. 打开命令行（Win+R 输入 cmd）

2. 下载并运行一键安装脚本：
```bash
cd C:\
curl -o install.bat https://raw.githubusercontent.com/wuyang2333/xhs/main/install.bat && install.bat
```

3. **安装跑完后先别启动**，从现有机器拷两个东西过来：

   - **数据库**：把现有机器的 `C:\xhs\data\spider_xhs.db` 拷到新机器相同位置
   - **图片文件**：把现有机器的 `C:\xhs\backend\app\storage\media\` 整个文件夹拷到新机器相同位置

   可以用 U 盘、网盘、或局域网共享拷。

4. 双击 `C:\xhs\XHS.bat`，选择后台启动

5. 浏览器打开 http://localhost:5173，用原有账号登录

### 完成后

- 所有账号、素材、草稿、指令、发布任务都在
- 模型配置也在，但 API Key 是加密存的，换机器后可能需要重新输入
- 以后更新：双击 `C:\xhs\update.bat`

---

## 日常使用

| 操作 | 怎么做 |
|---|---|
| 启动、停止、查看状态 | 双击 `XHS.bat` |
| 更新代码 | 双击 `update.bat` |
| 停止系统 | 在运行窗口按 Ctrl+C，或直接关窗口 |

## 注意事项

- **数据库和图片不在 git 里**——每台机器各自一份，代码更新不会覆盖数据
- **不要把 `.venv` 文件夹拷到新机器**——每台机器要自己创建虚拟环境
- **XHS.bat 管理窗口可以关闭**——服务会在后台继续运行；需要停止时再次双击并选择“停止服务”
- **如果启动报错**——看窗口里的英文错误信息，截图发给开发者

## 已知的 Windows 兼容性问题（已修复）

| 问题 | 原因 | 修复 |
|---|---|---|
| 配置文件中文注释导致启动崩 | Windows 用 GBK 编码读 .ini 文件 | 改成英文注释 |
| 脚本输出中文/emoji 崩 | subprocess 默认用系统编码 | 显式指定 UTF-8 |
| 手动拷文件更新 | 无版本控制 | 改用 git，双击 update.bat |
