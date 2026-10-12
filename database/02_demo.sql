-- Development examples only. Existing rows with these IDs are left unchanged.
INSERT IGNORE INTO tenants (tenant_id, name, industry) VALUES
('tenant_A', '示例电池工厂', '新能源电池'),
('tenant_B', '示例机械工厂', '机械制造'),
('tenant_C', '示例电子工厂', '电子制造');
INSERT IGNORE INTO plants (plant_id, tenant_id, name) VALUES
('plant_A1', 'tenant_A', '电池一厂'), ('plant_A2', 'tenant_A', '电池二厂'),
('plant_B1', 'tenant_B', '机械一厂'), ('plant_C1', 'tenant_C', '电子一厂');
INSERT IGNORE INTO production_lines (line_id, plant_id, name) VALUES
('line_a1', 'plant_A1', '模组装配线'), ('line_a2', 'plant_A2', '电芯封装线'),
('line_b1', 'plant_B1', '机械加工线'), ('line_c1', 'plant_C1', 'SMT生产线');
INSERT IGNORE INTO device_parameters (line_id, device_name, parameter, value, tolerance) VALUES
('line_a1', '激光焊接机', '电源', '380V/50Hz', '以设备铭牌为准'),
('line_a1', '激光焊接机', '额定功率', '120kW', '演示参数'),
('line_b1', '液压设备', '测试压力', '35.0MPa', '±0.5MPa（演示）');
INSERT IGNORE INTO fault_knowledge (tenant_id, device_type, code, description, solution, severity) VALUES
('tenant_A', '激光焊接机', 'F0012', '保护气体或光路异常（演示）',
 '检查保护气体纯度，校准聚焦镜位置。实际操作遵循经批准的现场规程。', '中'),
('tenant_A', '激光焊接机', 'F0013', '冷却水异常（演示）',
 '检查冷却水流量和回路堵塞情况。实际操作遵循经批准的现场规程。', '中');
