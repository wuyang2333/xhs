import {
  AndroidOutlined,
  ApiOutlined,
  CheckCircleFilled,
  DownloadOutlined,
  MobileOutlined,
  ReloadOutlined,
  TranslationOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Alert, Button, Input, Progress, Select, Space, Tag, Typography, message, theme } from "antd";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import {
  bindAndroidAccount,
  fetchAndroidAdbInstallStatus,
  fetchAndroidDevices,
  fetchAndroidUsers,
  fetchAndroidSetupStatus,
  installAndroidIme,
  startAndroidAdbInstall,
} from "../../lib/api";
import type { AdbInstallTask, AndroidDevice, AndroidSetupStatus, AndroidUserSpace, PlatformAccount } from "../../types";

const { Text, Paragraph } = Typography;

type AndroidBindPanelProps = {
  onBound: (account: PlatformAccount) => void;
};

function errorDetail(caught: unknown, fallback: string): string {
  const detail = (caught as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
  return detail || fallback;
}

type StepBlockProps = {
  index: number;
  title: string;
  owner: "auto" | "manual";
  done?: boolean;
  icon: ReactNode;
  children: ReactNode;
};

function StepBlock({ index, title, owner, done, icon, children }: StepBlockProps) {
  const { token } = theme.useToken();
  return (
    <div
      style={{
        border: `1px solid ${done ? token.colorSuccessBorder : token.colorBorderSecondary}`,
        background: done ? token.colorSuccessBg : token.colorFillQuaternary,
        borderRadius: token.borderRadiusLG,
        padding: 16,
      }}
    >
      <Space align="center" style={{ marginBottom: 10, flexWrap: "wrap" }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 22,
            height: 22,
            borderRadius: "50%",
            background: done ? token.colorSuccess : token.colorPrimary,
            color: token.colorWhite,
            fontSize: 12,
          }}
        >
          {index}
        </span>
        {icon}
        <Text strong>{title}</Text>
        {owner === "manual" ? (
          <Tag color="orange" icon={<UserOutlined />}>需你手动操作</Tag>
        ) : (
          <Tag color="blue">系统自动完成</Tag>
        )}
        {done ? <CheckCircleFilled style={{ color: token.colorSuccess }} /> : null}
      </Space>
      <div>{children}</div>
    </div>
  );
}

export function AndroidBindPanel({ onBound }: AndroidBindPanelProps) {
  const { token } = theme.useToken();
  const [devices, setDevices] = useState<AndroidDevice[]>([]);
  const [setupStatus, setSetupStatus] = useState<AndroidSetupStatus | null>(null);
  const [serial, setSerial] = useState<string>();
  const [nickname, setNickname] = useState("");
  const [androidUsers, setAndroidUsers] = useState<AndroidUserSpace[]>([]);
  const [androidUser, setAndroidUser] = useState("0");
  const [isDetecting, setIsDetecting] = useState(true);
  const [isBinding, setIsBinding] = useState(false);
  const [adbTask, setAdbTask] = useState<AdbInstallTask | null>(null);
  const [isStartingAdb, setIsStartingAdb] = useState(false);
  const [isInstallingIme, setIsInstallingIme] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const readyDevices = useMemo(() => devices.filter((item) => item.state === "device"), [devices]);
  const unauthorizedDevices = useMemo(
    () => devices.filter((item) => item.state !== "device"),
    [devices],
  );
  const adbReady = Boolean(setupStatus?.adb_available);
  const imeReady = Boolean(setupStatus?.ime_checked && setupStatus.ime_installed);

  const detect = useCallback(async (targetSerial?: string) => {
    setIsDetecting(true);
    setError(null);
    try {
      const status = await fetchAndroidSetupStatus(targetSerial);
      setSetupStatus(status);
      if (!status.adb_available) {
        setDevices([]);
        return;
      }
      const items = await fetchAndroidDevices();
      setDevices(items);
      const ready = items.filter((item) => item.state === "device");
      if (ready.length === 1) setSerial(ready[0].serial);
      else if (!ready.some((item) => item.serial === targetSerial)) setSerial(undefined);
    } catch (caught) {
      setError(errorDetail(caught, "无法读取 USB 安卓设备。请确认已开启 USB 调试并授权此电脑。"));
    } finally {
      setIsDetecting(false);
    }
  }, []);

  async function installAdb() {
    setIsStartingAdb(true);
    setError(null);
    try {
      const task = await startAndroidAdbInstall();
      setAdbTask(task);
      if (task.status === "success") {
        message.success(task.already_installed ? "ADB 已安装" : "ADB 安装完成");
        await detect(serial);
      }
    } catch (caught) {
      setError(errorDetail(caught, "无法启动 ADB 安装任务。"));
    } finally {
      setIsStartingAdb(false);
    }
  }

  async function installIme() {
    if (!serial) return;
    setIsInstallingIme(true);
    setError(null);
    try {
      const result = await installAndroidIme(serial);
      message.success(result.message || "中文输入法已安装");
      await detect(serial);
    } catch (caught) {
      setError(errorDetail(caught, "安装中文输入法失败。"));
    } finally {
      setIsInstallingIme(false);
    }
  }

  async function bind() {
    if (!serial) {
      setError("请选择一台已连接并授权的安卓手机。");
      return;
    }
    setIsBinding(true);
    setError(null);
    try {
      const account = await bindAndroidAccount({ serial, nickname: nickname.trim(), android_user: androidUser });
      message.success("安卓手机已绑定到账号矩阵");
      onBound(account);
    } catch (caught) {
      setError(errorDetail(caught, "绑定安卓手机失败。"));
    } finally {
      setIsBinding(false);
    }
  }

  useEffect(() => {
    void detect();
  }, [detect]);

  useEffect(() => {
    if (!serial) return;
    void fetchAndroidUsers(serial).then((items) => {
      setAndroidUsers(items);
      if (!items.some((item) => item.id === androidUser)) setAndroidUser(items[0]?.id || "0");
    }).catch(() => setAndroidUsers([{ id: "0", name: "主空间" }]));
  }, [serial]);

  // 安装是长任务，轮询到终态为止。
  useEffect(() => {
    if (!adbTask || (adbTask.status !== "pending" && adbTask.status !== "running")) return;
    const timer = setTimeout(() => {
      void (async () => {
        try {
          const next = await fetchAndroidAdbInstallStatus(adbTask.id);
          setAdbTask(next);
          if (next.status === "success") {
            message.success("ADB 安装完成");
            await detect(serial);
          }
        } catch (caught) {
          setError(errorDetail(caught, "无法读取安装进度。"));
        }
      })();
    }, 1500);
    return () => clearTimeout(timer);
  }, [adbTask, detect, serial]);

  // 选中手机后要重新查这台机器的输入法状态。
  useEffect(() => {
    if (!serial || !adbReady) return;
    if (setupStatus?.ime_serial === serial) return;
    void detect(serial);
  }, [serial, adbReady, setupStatus?.ime_serial, detect]);

  const adbInstalling = adbTask?.status === "pending" || adbTask?.status === "running";

  return (
    <Space direction="vertical" size={12} style={{ width: "100%" }}>
      <Alert
        type="info"
        showIcon
        message="按顺序完成 6 步即可绑定手机"
        description="第 2、3 步必须你在手机上手动操作（安卓安全限制，程序无法代替）；其余步骤点按钮由系统自动完成。"
      />
      {error ? <Alert type="error" showIcon message={error} closable onClose={() => setError(null)} /> : null}

      <StepBlock index={1} title="安装 ADB 连接工具" owner="auto" done={adbReady} icon={<DownloadOutlined />}>
        {adbReady ? (
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            已就绪：<Text code>{setupStatus?.adb_path}</Text>
          </Paragraph>
        ) : (
          <>
            <Paragraph type="secondary" style={{ marginBottom: 8 }}>
              这台电脑还没有 ADB。点下面的按钮从谷歌官网自动下载安装（约 15 MB），全程无需命令行。
            </Paragraph>
            {adbTask?.status === "failed" ? (
              <Alert type="error" showIcon style={{ marginBottom: 8 }} message="安装失败" description={adbTask.error} />
            ) : null}
            {adbInstalling ? (
              <div style={{ marginBottom: 8 }}>
                <Progress percent={adbTask?.progress ?? 0} status="active" />
                <Text type="secondary">{adbTask?.message}</Text>
              </div>
            ) : null}
            <Button
              type="primary"
              icon={<DownloadOutlined />}
              onClick={installAdb}
              loading={isStartingAdb || adbInstalling}
              disabled={setupStatus ? !setupStatus.adb_installable : false}
            >
              {adbTask?.status === "failed" ? "重试安装 ADB" : "一键安装 ADB"}
            </Button>
            {setupStatus && !setupStatus.adb_installable ? (
              <Text type="warning" style={{ marginLeft: 8 }}>
                当前系统（{setupStatus.platform}）无自动安装包，请手动安装 Platform Tools。
              </Text>
            ) : null}
          </>
        )}
      </StepBlock>

      <StepBlock index={2} title="在手机上打开 USB 调试" owner="manual" icon={<MobileOutlined />}>
        <Paragraph type="secondary" style={{ marginBottom: 4 }}>
          这一步程序无法代替，请在手机上依次操作：
        </Paragraph>
        <ol style={{ margin: 0, paddingLeft: 20, color: token.colorTextSecondary }}>
          <li>设置 → 关于手机 → 连续点击「版本号」7 次，提示「已进入开发者模式」</li>
          <li>返回设置 → 系统与更新（小米为「更多设置」）→ 开发者选项</li>
          <li>打开「USB 调试」开关</li>
          <li>小米/红米还需额外打开「USB 调试（安全设置）」，否则无法自动操作界面</li>
        </ol>
      </StepBlock>

      <StepBlock index={3} title="插上数据线并在手机上点「允许」" owner="manual" icon={<ApiOutlined />}>
        <Paragraph type="secondary" style={{ marginBottom: 4 }}>
          用数据线把手机连到这台电脑，然后：
        </Paragraph>
        <ol style={{ margin: 0, paddingLeft: 20, color: token.colorTextSecondary }}>
          <li><Text strong>保持手机解锁</Text>，锁屏状态下不会弹出授权窗口</li>
          <li>把 USB 用途改为「文件传输 / MTP」，选「仅充电」时弹窗可能不出现</li>
          <li>看到「允许 USB 调试吗？」时，<Text strong>勾选「一直允许使用这台计算机进行调试」</Text>再点「允许」</li>
        </ol>
      </StepBlock>

      <StepBlock index={4} title="检测手机" owner="auto" done={readyDevices.length > 0} icon={<ReloadOutlined />}>
        <Button
          icon={<ReloadOutlined />}
          onClick={() => void detect(serial)}
          loading={isDetecting}
          disabled={!adbReady}
          style={{ marginBottom: unauthorizedDevices.length || readyDevices.length ? 10 : 0 }}
        >
          检测已连接的手机
        </Button>
        {!adbReady ? <Text type="secondary" style={{ marginLeft: 8 }}>请先完成第 1 步</Text> : null}
        {unauthorizedDevices.length > 0 ? (
          <Alert
            type="warning"
            showIcon
            style={{ marginBottom: 8 }}
            message={`已检测到 ${unauthorizedDevices.length} 台手机，但还没授权这台电脑`}
            description={
              <div>
                <div>
                  <Text strong>请拿起手机</Text>：解锁屏幕，找到「允许 USB 调试吗？」弹窗，勾选「一直允许」后点「允许」，然后回到这里再点一次检测。
                </div>
                <div style={{ marginTop: 4 }}>
                  没看到弹窗？把 USB 用途改成「文件传输」，或拔线重插一次。
                </div>
                <div style={{ marginTop: 4 }}>
                  {unauthorizedDevices
                    .map((item) => `${item.model || "Android"} · ${item.serial.slice(-6)}（${item.state}）`)
                    .join("；")}
                </div>
              </div>
            }
          />
        ) : null}
        {readyDevices.length > 0 ? (
          <Alert type="success" showIcon message={`${readyDevices.length} 台手机已授权，可以继续`} />
        ) : null}
        {adbReady && !isDetecting && devices.length === 0 ? (
          <Text type="secondary">没检测到任何手机。请确认数据线已插好、第 2 步的 USB 调试已打开。</Text>
        ) : null}
      </StepBlock>

      <StepBlock index={5} title="安装中文输入法" owner="auto" done={imeReady} icon={<TranslationOutlined />}>
        <Paragraph type="secondary" style={{ marginBottom: 8 }}>
          手机自带输入法无法被程序控制，不装这个就<Text strong>打不出中文标题和正文</Text>。
        </Paragraph>
        {imeReady ? (
          <Text type="success">这台手机已安装并启用中文输入法。</Text>
        ) : (
          <Button
            type="primary"
            icon={<TranslationOutlined />}
            onClick={installIme}
            loading={isInstallingIme}
            disabled={!serial || readyDevices.length === 0}
          >
            为选中手机一键安装中文输入法
          </Button>
        )}
        {!serial ? <Text type="secondary" style={{ marginLeft: 8 }}>请先在第 6 步选择手机</Text> : null}
      </StepBlock>

      <StepBlock index={6} title="选择手机并绑定账号" owner="manual" icon={<AndroidOutlined />}>
        <div style={{ marginBottom: 10 }}>
          <Text strong style={{ display: "block", marginBottom: 6 }}>安卓设备</Text>
          <Select
            value={serial}
            onChange={setSerial}
            style={{ width: "100%" }}
            disabled={readyDevices.length === 0}
            options={readyDevices.map((item) => ({
              value: item.serial,
              label: `${item.model || "Android"} · ${item.serial.slice(-6)}`,
            }))}
            placeholder={readyDevices.length ? "选择已授权设备" : "完成前 4 步后这里会出现手机"}
          />
        </div>
        <div style={{ marginBottom: 10 }}>
          <Text strong style={{ display: "block", marginBottom: 6 }}>应用分身空间</Text>
          <Select
            value={androidUser}
            onChange={setAndroidUser}
            style={{ width: "100%", marginBottom: 10 }}
            options={androidUsers.map((item) => ({ value: item.id, label: `${item.name}（用户 ${item.id}）` }))}
            placeholder="选择主空间或应用分身"
          />
          <Text strong style={{ display: "block", marginBottom: 6 }}>备注名称（可选）</Text>
          <Input
            value={nickname}
            onChange={(event) => setNickname(event.target.value)}
            placeholder="例如：内容发布机 01"
          />
        </div>
        <Button type="primary" block icon={<AndroidOutlined />} onClick={bind} loading={isBinding} disabled={!serial}>
          绑定安卓手机账号
        </Button>
        <Paragraph type="secondary" style={{ margin: "8px 0 0" }}>
          绑定前请确认手机上已安装并登录小红书。一个账号固定使用一台真实手机。
        </Paragraph>
      </StepBlock>
    </Space>
  );
}
