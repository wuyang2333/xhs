import { KeyOutlined, PlusOutlined, SafetyCertificateOutlined, UserSwitchOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, Modal, Popconfirm, Space, Table, Tag, Typography, message } from "antd";
import { useCallback, useEffect, useState } from "react";

import { PageHeader } from "../../components/layout/app-shell";
import { useAuth } from "../../hooks/use-auth";
import { createSubAdmin, fetchAdminUsers, resetUserPassword, setUserAdmin } from "../../lib/api";
import type { AdminUser } from "../../types";

const { Text } = Typography;

export function AdminUsersPage() {
  const auth = useAuth();
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [resetTarget, setResetTarget] = useState<AdminUser | null>(null);
  const [subAdminOpen, setSubAdminOpen] = useState(false);
  const [form] = Form.useForm();
  const [subAdminForm] = Form.useForm();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchAdminUsers();
      setUsers(data.items);
    } catch {
      message.error("加载用户列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function handleResetPassword(values: { password: string }) {
    if (!resetTarget) return;
    try {
      await resetUserPassword(resetTarget.id, values.password);
      message.success(`已重置「${resetTarget.username}」的密码`);
      setResetTarget(null);
      form.resetFields();
    } catch {
      message.error("重置密码失败");
    }
  }

  async function handleCreateSubAdmin(values: { username: string; password: string }) {
    try {
      await createSubAdmin(values.username, values.password);
      message.success(`子管理员「${values.username}」已创建，登录后共享主账号数据`);
      setSubAdminOpen(false);
      subAdminForm.resetFields();
      await load();
    } catch {
      message.error("创建子管理员失败");
    }
  }

  async function handleToggleAdmin(user: AdminUser) {
    try {
      await setUserAdmin(user.id, !user.is_admin);
      message.success(user.is_admin ? `已取消「${user.username}」的管理员权限` : `已将「${user.username}」设为管理员`);
      await load();
    } catch {
      message.error("操作失败");
    }
  }

  if (!auth.user?.is_admin) {
    return (
      <div>
        <PageHeader eyebrow="账号管理" title="账号管理" description="需要管理员权限" />
        <Card>
          <Text type="secondary">当前账号不是管理员，无法查看用户管理。</Text>
        </Card>
      </div>
    );
  }

  return (
    <div>
      <PageHeader
        eyebrow="系统管理"
        title="账号管理"
        description="查看注册用户、重置密码、设置管理员、创建共享数据的子管理员。"
        action={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setSubAdminOpen(true)}>
            新建子管理员
          </Button>
        }
      />
      <Card loading={loading}>
        <Table<AdminUser>
          rowKey="id"
          dataSource={users}
          pagination={false}
          columns={[
            {
              title: "ID",
              dataIndex: "id",
              width: 80,
            },
            {
              title: "用户名",
              dataIndex: "username",
              render: (value: string) => <Text strong>{value}</Text>,
            },
            {
              title: "角色",
              dataIndex: "is_admin",
              width: 140,
              render: (isAdmin: boolean) =>
                isAdmin ? <Tag color="gold">管理员</Tag> : <Tag>普通用户</Tag>,
            },
            {
              title: "注册时间",
              dataIndex: "created_at",
              width: 200,
              render: (value: string | null) => (value ? new Date(value).toLocaleString("zh-CN") : "-"),
            },
            {
              title: "操作",
              width: 240,
              render: (_, record) => (
                <Space>
                  <Button size="small" icon={<KeyOutlined />} onClick={() => setResetTarget(record)}>
                    重置密码
                  </Button>
                  <Popconfirm
                    title={record.is_admin ? `取消「${record.username}」的管理员权限？` : `将「${record.username}」设为管理员？`}
                    onConfirm={() => void handleToggleAdmin(record)}
                  >
                    <Button size="small" icon={<SafetyCertificateOutlined />}>
                      {record.is_admin ? "取消管理员" : "设为管理员"}
                    </Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Modal
        title="新建子管理员"
        open={subAdminOpen}
        onCancel={() => {
          setSubAdminOpen(false);
          subAdminForm.resetFields();
        }}
        onOk={() => subAdminForm.submit()}
        okText="创建"
        cancelText="取消"
      >
        <Form form={subAdminForm} layout="vertical" onFinish={handleCreateSubAdmin}>
          <Form.Item
            name="username"
            label="用户名"
            rules={[
              { required: true, message: "请输入用户名" },
              { min: 2, message: "用户名至少 2 个字符" },
            ]}
          >
            <Input placeholder="登录账号" autoComplete="off" />
          </Form.Item>
          <Form.Item
            name="password"
            label="初始密码"
            rules={[
              { required: true, message: "请输入初始密码" },
              { min: 6, message: "密码至少 6 个字符" },
            ]}
          >
            <Input.Password placeholder="至少 6 个字符" autoComplete="new-password" />
          </Form.Item>
          <Text type="secondary" style={{ fontSize: 12 }}>
            子管理员登录后共享主账号的全部数据（模型配置、内容库、账号矩阵等）。
          </Text>
        </Form>
      </Modal>

      <Modal
        title={`重置密码 · ${resetTarget?.username ?? ""}`}
        open={resetTarget !== null}
        onCancel={() => {
          setResetTarget(null);
          form.resetFields();
        }}
        onOk={() => form.submit()}
        okText="重置"
        cancelText="取消"
      >
        <Form form={form} layout="vertical" onFinish={handleResetPassword}>
          <Form.Item
            name="password"
            label="新密码"
            rules={[
              { required: true, message: "请输入新密码" },
              { min: 6, message: "密码至少 6 个字符" },
            ]}
          >
            <Input.Password placeholder="至少 6 个字符" autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}