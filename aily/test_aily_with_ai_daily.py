#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aily应用ID测试脚本 - 针对"AI日报new"机器人
专门测试各种可能的Aily应用ID格式，找到正确的aily.app_id
"""

import requests
import json
import time
from typing import Dict, List, Optional, Tuple

class AilyAppIdTester:
    """Aily应用ID测试器"""
    
    def __init__(self, app_id: str, app_secret: str):
        """
        初始化测试器
        
        Args:
            app_id: 飞书应用ID (cli_xxx)
            app_secret: 飞书应用密钥
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = "https://open.feishu.cn/open-apis"
        self.tenant_access_token = None
        self.token_expire_time = 0
        
        # 可能的Aily应用ID（按可能性排序）
        self.possible_aily_app_ids = [
            "spring_general_assistant__c",  # 通用助手（最可能）
            "spring_ai_assistant__c",       # AI助手
            "spring_work_assistant__c",     # 工作助手
            "spring_assistant__c",          # 助手
            "spring_ai_daily__c",           # AI日报（自定义格式）
            "spring_ai_daily_new__c",       # AI日报new（自定义格式）
            "spring_ai_news__c",            # AI新闻
            "spring_content_assistant__c",  # 内容助手
            "spring_creative_assistant__c", # 创意助手
            "spring_design_assistant__c",   # 设计助手
        ]
        
        # 测试的biz_user_id（您的用户ID）
        self.biz_user_id = "7594288708510125236"
        
    def get_tenant_access_token(self) -> str:
        """获取租户访问令牌"""
        current_time = time.time()
        
        # 如果token还有效，直接返回
        if self.tenant_access_token and current_time < self.token_expire_time:
            return self.tenant_access_token
        
        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        data = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        
        try:
            response = requests.post(url, headers=headers, json=data, timeout=10)
            response.raise_for_status()
            result = response.json()
            
            if result.get("code") == 0:
                self.tenant_access_token = result["tenant_access_token"]
                self.token_expire_time = current_time + result["expire"] - 300  # 提前5分钟过期
                print(f"✅ 成功获取tenant_access_token，有效期: {result['expire']}秒")
                return self.tenant_access_token
            else:
                print(f"❌ 获取token失败: {result.get('msg', '未知错误')}")
                return None
                
        except Exception as e:
            print(f"❌ 获取token异常: {str(e)}")
            return None
    
    def test_app_id(self, aily_app_id: str) -> Tuple[bool, str, Optional[Dict]]:
        """
        测试特定的Aily应用ID
        
        Args:
            aily_app_id: 要测试的Aily应用ID
            
        Returns:
            (是否成功, 消息, 响应数据)
        """
        token = self.get_tenant_access_token()
        if not token:
            return False, "获取token失败", None
        
        # 测试1: 创建会话
        session_url = f"{self.base_url}/aily/v1/sessions"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        session_data = {
            "app_id": aily_app_id,
            "biz_user_id": self.biz_user_id,
            "name": "测试会话"
        }
        
        try:
            response = requests.post(session_url, headers=headers, json=session_data, timeout=10)
            response_data = response.json()
            
            if response.status_code == 200 and response_data.get("code") == 0:
                session_id = response_data["data"]["session"]["id"]
                print(f"  ✅ 成功创建会话: {session_id}")
                
                # 测试2: 发送消息
                message_url = f"{self.base_url}/aily/v1/sessions/{session_id}/messages"
                message_data = {
                    "content": "你好，请生成一张关于科技创新的图片",
                    "role": "user"
                }
                
                message_response = requests.post(message_url, headers=headers, json=message_data, timeout=10)
                message_data = message_response.json()
                
                if message_response.status_code == 200 and message_data.get("code") == 0:
                    message_id = message_data["data"]["message"]["id"]
                    print(f"  ✅ 成功发送消息: {message_id}")
                    
                    # 测试3: 创建运行（图片生成）
                    run_url = f"{self.base_url}/aily/v1/sessions/{session_id}/runs"
                    run_data = {
                        "model": "seedream",
                        "tools": ["image_generation"]
                    }
                    
                    run_response = requests.post(run_url, headers=headers, json=run_data, timeout=10)
                    run_data = run_response.json()
                    
                    if run_response.status_code == 200 and run_data.get("code") == 0:
                        run_id = run_data["data"]["run"]["id"]
                        print(f"  ✅ 成功创建运行: {run_id}")
                        return True, "所有测试通过", response_data
                    else:
                        return True, "会话和消息测试通过，但运行创建失败", response_data
                else:
                    return True, "会话测试通过，但消息发送失败", response_data
                    
            elif response.status_code == 404:
                return False, "API接口不存在", None
            elif response.status_code == 403:
                return False, "权限不足", response_data
            elif response.status_code == 400:
                error_msg = response_data.get("msg", "参数错误")
                if "app_id" in error_msg.lower():
                    return False, "应用ID无效", response_data
                else:
                    return False, f"参数错误: {error_msg}", response_data
            else:
                return False, f"HTTP {response.status_code}: {response_data.get('msg', '未知错误')}", response_data
                
        except requests.exceptions.Timeout:
            return False, "请求超时", None
        except Exception as e:
            return False, f"异常: {str(e)}", None
    
    def test_all_possible_ids(self) -> Dict[str, Tuple[bool, str]]:
        """测试所有可能的Aily应用ID"""
        results = {}
        
        print(f"\n🔍 开始测试所有可能的Aily应用ID...")
        print(f"📋 共 {len(self.possible_aily_app_ids)} 个ID需要测试")
        print("=" * 60)
        
        for i, aily_app_id in enumerate(self.possible_aily_app_ids, 1):
            print(f"\n[{i}/{len(self.possible_aily_app_ids)}] 测试: {aily_app_id}")
            print("-" * 40)
            
            success, message, data = self.test_app_id(aily_app_id)
            results[aily_app_id] = (success, message)
            
            if success:
                print(f"🎉 测试结果: {message}")
                if data:
                    print(f"📊 响应数据: {json.dumps(data, ensure_ascii=False, indent=2)[:200]}...")
            else:
                print(f"❌ 测试结果: {message}")
        
        return results
    
    def generate_report(self, results: Dict[str, Tuple[bool, str]]) -> str:
        """生成测试报告"""
        report_lines = []
        report_lines.append("=" * 60)
        report_lines.append("📊 Aily应用ID测试报告")
        report_lines.append("=" * 60)
        report_lines.append(f"测试时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"机器人ID: {self.app_id}")
        report_lines.append(f"用户ID: {self.biz_user_id}")
        report_lines.append("")
        
        # 统计结果
        successful_ids = [id for id, (success, _) in results.items() if success]
        failed_ids = [id for id, (success, _) in results.items() if not success]
        
        report_lines.append("📈 测试统计:")
        report_lines.append(f"  成功: {len(successful_ids)} 个")
        report_lines.append(f"  失败: {len(failed_ids)} 个")
        report_lines.append("")
        
        if successful_ids:
            report_lines.append("✅ 成功的应用ID:")
            for app_id in successful_ids:
                success, message = results[app_id]
                report_lines.append(f"  • {app_id} - {message}")
            report_lines.append("")
            
            # 推荐最佳ID
            best_id = successful_ids[0]  # 第一个成功的ID（按可能性排序）
            report_lines.append("💡 推荐使用:")
            report_lines.append(f"  {best_id}")
            report_lines.append("")
            report_lines.append("📝 配置示例:")
            report_lines.append(f"  AILY_APP_ID = '{best_id}'")
            report_lines.append(f"  BIZ_USER_ID = '{self.biz_user_id}'")
            report_lines.append(f"  BOT_APP_ID = '{self.app_id}'")
        else:
            report_lines.append("❌ 没有找到有效的Aily应用ID")
            report_lines.append("")
            report_lines.append("🔧 建议:")
            report_lines.append("  1. 确认飞书开放平台已正确开通Aily权限")
            report_lines.append("  2. 联系管理员确认Aily应用ID")
            report_lines.append("  3. 等待几分钟后重试（权限同步可能有延迟）")
        
        report_lines.append("")
        report_lines.append("📋 详细结果:")
        for app_id, (success, message) in results.items():
            status = "✅" if success else "❌"
            report_lines.append(f"  {status} {app_id}: {message}")
        
        report_lines.append("=" * 60)
        
        return "\n".join(report_lines)

def main():
    """主函数"""
    print("🔍 Aily应用ID测试工具 - 针对'AI日报new'机器人")
    print("=" * 60)
    
    # 使用您提供的凭证
    app_id = "cli_a9f8b4203eb8dcca"
    app_secret = "aViEyBopvecfzOnKICvb3elkWKTeBefO"
    
    print(f"📱 机器人ID: {app_id}")
    print(f"🔑 用户ID: 7594288708510125236 (沈正一)")
    print("")
    
    # 创建测试器
    tester = AilyAppIdTester(app_id, app_secret)
    
    # 测试所有可能的ID
    results = tester.test_all_possible_ids()
    
    # 生成报告
    report = tester.generate_report(results)
    print(report)
    
    # 保存报告到文件
    report_file = "/home/workspace/aily_app_id_test_report.txt"
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(report)
    
    print(f"\n📄 详细报告已保存到: {report_file}")
    
    # 如果有成功的ID，创建配置示例
    successful_ids = [id for id, (success, _) in results.items() if success]
    if successful_ids:
        best_id = successful_ids[0]
        config_file = "/home/workspace/aily_config_example.py"
        config_content = f'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Aily配置示例 - 基于测试结果
"""

class AilyConfig:
    """Aily配置类"""
    
    # 机器人凭证（飞书开放平台）
    BOT_APP_ID = "{app_id}"
    BOT_APP_SECRET = "{app_secret}"
    
    # Aily应用ID（通过测试找到的）
    AILY_APP_ID = "{best_id}"
    
    # 业务用户ID（您的飞书ID）
    BIZ_USER_ID = "7594288708510125236"
    
    # API基础URL
    BASE_URL = "https://open.feishu.cn/open-apis"
    
    @classmethod
    def get_credentials(cls):
        """获取所有凭证"""
        return {{
            "bot_app_id": cls.BOT_APP_ID,
            "bot_app_secret": cls.BOT_APP_SECRET,
            "aily_app_id": cls.AILY_APP_ID,
            "biz_user_id": cls.BIZ_USER_ID
        }}

if __name__ == "__main__":
    config = AilyConfig()
    print("✅ Aily配置信息:")
    for key, value in config.get_credentials().items():
        print(f"  {key}: {value}")
'''
        
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(config_content)
        
        print(f"⚙️  配置示例已保存到: {config_file}")
        print("\n🚀 下一步:")
        print("  1. 使用找到的Aily应用ID配置您的脚本")
        print("  2. 运行 aily_cover_sync.py 测试图片生成功能")
        print("  3. 验证封面图生成和写入多维表格的完整流程")

if __name__ == "__main__":
    main()