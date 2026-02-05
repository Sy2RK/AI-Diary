#!/usr/bin/env python3
"""
通过API获取Aily应用列表的脚本
用于解决无法通过界面查看aily.app_id的问题
"""

import requests
import json
import os
from typing import Dict, List, Optional

class AilyAppFetcher:
    """Aily应用列表获取器"""
    
    def __init__(self, app_id: str, app_secret: str):
        """
        初始化
        
        Args:
            app_id: 飞书开放平台应用ID
            app_secret: 飞书开放平台应用密钥
        """
        self.app_id = app_id
        self.app_secret = app_secret
        self.base_url = "https://open.feishu.cn/open-apis"
        self.tenant_access_token = None
        
    def get_tenant_access_token(self) -> str:
        """获取租户访问令牌"""
        url = f"{self.base_url}/auth/v3/tenant_access_token/internal"
        payload = {
            "app_id": self.app_id,
            "app_secret": self.app_secret
        }
        
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            if data.get("code") == 0:
                self.tenant_access_token = data["tenant_access_token"]
                print(f"✅ 成功获取tenant_access_token，有效期: {data.get('expire', 7200)}秒")
                return self.tenant_access_token
            else:
                print(f"❌ 获取token失败: {data.get('msg', '未知错误')}")
                return None
        except Exception as e:
            print(f"❌ 获取token时出错: {str(e)}")
            return None
    
    def get_aily_applications(self) -> List[Dict]:
        """获取Aily应用列表"""
        if not self.tenant_access_token:
            print("❌ 请先获取tenant_access_token")
            return []
        
        # 方法1: 尝试通过Aily API获取应用列表
        url = f"{self.base_url}/aily/v1/applications"
        headers = {
            "Authorization": f"Bearer {self.tenant_access_token}",
            "Content-Type": "application/json"
        }
        
        try:
            response = requests.get(url, headers=headers, timeout=10)
            
            if response.status_code == 200:
                data = response.json()
                if data.get("code") == 0:
                    apps = data.get("data", {}).get("items", [])
                    print(f"✅ 通过Aily API获取到 {len(apps)} 个应用")
                    return apps
                else:
                    print(f"⚠️ Aily API返回错误: {data.get('msg', '未知错误')}")
            else:
                print(f"⚠️ Aily API请求失败: {response.status_code}")
                
        except Exception as e:
            print(f"⚠️ 调用Aily API时出错: {str(e)}")
        
        # 方法2: 如果Aily API不可用，尝试其他方法
        print("尝试通过其他接口获取应用信息...")
        return self._try_alternative_methods()
    
    def _try_alternative_methods(self) -> List[Dict]:
        """尝试其他方法获取应用信息"""
        methods = [
            self._get_applications_via_platform,
            self._get_applications_via_workspace
        ]
        
        for method in methods:
            try:
                apps = method()
                if apps:
                    return apps
            except Exception as e:
                print(f"方法 {method.__name__} 失败: {str(e)}")
                continue
        
        return []
    
    def _get_applications_via_platform(self) -> List[Dict]:
        """通过平台API获取应用"""
        url = f"{self.base_url}/application/v6/applications"
        headers = {
            "Authorization": f"Bearer {self.tenant_access_token}",
            "Content-Type": "application/json"
        }
        
        params = {
            "page_size": 100,
            "page_token": ""
        }
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 0:
                all_apps = data.get("data", {}).get("items", [])
                # 过滤出包含Aily权限的应用
                aily_apps = []
                for app in all_apps:
                    if self._has_aily_permission(app):
                        aily_apps.append(app)
                print(f"✅ 从平台API找到 {len(aily_apps)} 个包含Aily权限的应用")
                return aily_apps
        
        return []
    
    def _get_applications_via_workspace(self) -> List[Dict]:
        """通过工作区API获取应用"""
        url = f"{self.base_url}/workspace/v1/applications"
        headers = {
            "Authorization": f"Bearer {self.tenant_access_token}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 0:
                apps = data.get("data", {}).get("items", [])
                print(f"✅ 从工作区API获取到 {len(apps)} 个应用")
                return apps
        
        return []
    
    def _has_aily_permission(self, app: Dict) -> bool:
        """检查应用是否包含Aily权限"""
        scopes = app.get("scopes", [])
        aily_scopes = ["aily:session", "aily:message", "aily:run"]
        return any(scope in aily_scopes for scope in scopes)
    
    def print_applications(self, apps: List[Dict]):
        """打印应用列表"""
        if not apps:
            print("❌ 未找到任何Aily应用")
            return
        
        print("\n" + "="*80)
        print("📋 您的Aily应用列表")
        print("="*80)
        
        for i, app in enumerate(apps, 1):
            print(f"\n【应用 {i}】")
            print(f"  应用名称: {app.get('name', '未知')}")
            print(f"  应用ID: {app.get('app_id', '未知')}")
            print(f"  应用类型: {app.get('app_type', '未知')}")
            
            # Aily特定的信息
            if 'aily_app_id' in app:
                print(f"  Aily应用ID: {app.get('aily_app_id')}")
            
            # 权限信息
            scopes = app.get('scopes', [])
            aily_scopes = [s for s in scopes if s.startswith('aily:')]
            if aily_scopes:
                print(f"  Aily权限: {', '.join(aily_scopes)}")
            
            # 描述信息
            if 'description' in app:
                print(f"  描述: {app.get('description')}")
            
            print("-"*40)

def main():
    """主函数"""
    print("🔍 Aily应用列表获取工具")
    print("="*80)
    
    # 从环境变量或用户输入获取凭证
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    
    if not app_id or not app_secret:
        print("\n请提供您的飞书应用凭证：")
        app_id = input("请输入 App ID: ").strip()
        app_secret = input("请输入 App Secret: ").strip()
    
    if not app_id or not app_secret:
        print("❌ 必须提供App ID和App Secret")
        return
    
    # 创建获取器实例
    fetcher = AilyAppFetcher(app_id, app_secret)
    
    # 获取token
    print("\n1️⃣ 获取访问令牌...")
    token = fetcher.get_tenant_access_token()
    if not token:
        return
    
    # 获取应用列表
    print("\n2️⃣ 获取Aily应用列表...")
    apps = fetcher.get_aily_applications()
    
    # 打印结果
    print("\n3️⃣ 分析结果...")
    fetcher.print_applications(apps)
    
    # 提供建议
    print("\n" + "="*80)
    print("💡 使用建议")
    print("="*80)
    
    if apps:
        print("""
✅ 找到了您的Aily应用！请按以下步骤操作：

1. 从上面的列表中找出您的Aily应用
2. 如果看到类似 'spring_xxx__c' 格式的ID，那就是您的 aily.app_id
3. 如果没有找到特定格式，可能是系统预定义的应用

如果您要调用"aily工作助手"通用助手，可以尝试使用：
- app_id: "spring_general_assistant__c"（通用助手的可能ID）
- 或使用您自己的应用ID
        """)
    else:
        print("""
⚠️ 未找到明确的Aily应用，可能是以下情况：

情况A：您使用的是"aily工作助手"通用助手
   - 可以尝试使用通用助手的ID: "spring_general_assistant__c"
   - 或联系管理员确认具体ID

情况B：您的应用尚未在API中列出
   - 请登录飞书开放平台确认Aily权限已正确开通
   - 等待几分钟后重试

情况C：需要特定的权限配置
   - 确保您的应用有 aily:* 相关权限
   - 可能需要重新授权
        """)

if __name__ == "__main__":
    main()