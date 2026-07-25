"""
AI API 连通性测试模块
支持多种AI提供商的连通性测试
"""

import requests
import json
from typing import Dict, Optional, Tuple


class AIConnectivityTester:
    """AI API 连通性测试器"""

    # 各平台的测试端点
    ENDPOINTS = {
        'SILICON': 'https://api.siliconflow.cn/v1/chat/completions',
        'DEEPSEEK': 'https://api.deepseek.com/v1/chat/completions',
        'CHATGLM': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        'TONGYI': 'https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation',
        'XINGHUO': 'https://spark-api-open.xf-yun.com/v1/chat/completions',
        'DOUBAO': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
        'METAAI': 'https://api.meta.ai/v1/chat/completions',
        'OPENAI': 'https://api.openai.com/v1/chat/completions',
        'OTHER': None,  # 使用用户提供的URL
    }

    # 默认测试模型
    DEFAULT_MODELS = {
        'SILICON': 'Qwen/Qwen2.5-7B-Instruct',
        'DEEPSEEK': 'deepseek-chat',
        'CHATGLM': 'glm-4-flash',
        'TONGYI': 'qwen-turbo',
        'XINGHUO': 'generalv3.5',
        'DOUBAO': 'doubao-pro-32k',
        'METAAI': 'llama-3-70b',
        'OPENAI': 'gpt-3.5-turbo',
        'OTHER': None,
    }

    # 平台名称映射
    PLATFORM_NAMES = {
        'SILICON': '硅基流动',
        'DEEPSEEK': 'DeepSeek',
        'CHATGLM': '智谱AI',
        'TONGYI': '通义千问',
        'XINGHUO': '讯飞星火',
        'DOUBAO': '豆包',
        'METAAI': 'Meta AI',
        'OPENAI': 'OpenAI',
        'OTHER': '自定义',
    }

    @classmethod
    def _normalize_chat_endpoint(cls, provider: str, api_url: str) -> str:
        """Return a usable chat-completions endpoint for OpenAI-compatible APIs."""
        endpoint = (api_url or cls.ENDPOINTS.get(provider) or "").strip().rstrip("/")
        if not endpoint:
            return ""
        if provider == 'TONGYI':
            return endpoint
        if endpoint.endswith('/chat/completions'):
            return endpoint
        if endpoint.endswith('/models'):
            return endpoint[:-len('/models')] + '/chat/completions'
        if endpoint.endswith('/v1'):
            return endpoint + '/chat/completions'
        return endpoint

    @classmethod
    def _build_models_endpoint(cls, api_url: str) -> str:
        """Build the matching /models endpoint without duplicating /v1."""
        endpoint = (api_url or "").strip().rstrip("/")
        if not endpoint:
            return ""
        if endpoint.endswith('/models'):
            return endpoint
        if endpoint.endswith('/chat/completions'):
            return endpoint[:-len('/chat/completions')] + '/models'
        if endpoint.endswith('/v1'):
            return endpoint + '/models'
        return endpoint + '/v1/models'

    @classmethod
    def test_connectivity(
        cls,
        provider: str,
        api_url: str,
        api_key: str,
        model: str,
        timeout: int = 30
    ) -> Tuple[bool, str, Dict]:
        """
        测试AI API连通性

        Args:
            provider: AI提供商代码 (SILICON, DEEPSEEK, etc.)
            api_url: API接口地址
            api_key: API密钥
            model: 模型名称
            timeout: 请求超时时间(秒)

        Returns:
            Tuple[success: bool, message: str, details: Dict]
        """
        if not api_key or not api_key.strip():
            return False, "API Key 不能为空", {"error": "missing_api_key"}

        # 确定测试端点
        endpoint = cls._normalize_chat_endpoint(provider, api_url)
        if not endpoint:
            return False, "未提供有效的API地址", {"error": "missing_endpoint"}

        # 确定测试模型
        test_model = model if model else cls.DEFAULT_MODELS.get(provider, 'gpt-3.5-turbo')

        # 构建请求头
        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

        # 构建请求体
        payload = cls._build_payload(provider, test_model)

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout
            )

            # 处理响应
            return cls._handle_response(response, provider)

        except requests.exceptions.Timeout:
            return False, f"请求超时 ({timeout}秒)", {
                "error": "timeout",
                "provider": provider,
                "endpoint": endpoint
            }
        except requests.exceptions.ConnectionError:
            return False, "网络连接失败，请检查网络", {
                "error": "connection_error",
                "provider": provider,
                "endpoint": endpoint
            }
        except requests.exceptions.RequestException as e:
            return False, f"请求异常: {str(e)}", {
                "error": "request_exception",
                "message": str(e),
                "provider": provider
            }
        except Exception as e:
            return False, f"测试失败: {str(e)}", {
                "error": "unknown",
                "message": str(e),
                "provider": provider
            }

    @classmethod
    def _build_payload(cls, provider: str, model: str) -> Dict:
        """构建测试请求体"""
        # 基础消息
        messages = [{"role": "user", "content": "你好，这是一个连通性测试。请回复'测试成功'。"}]

        # 根据不同提供商调整payload
        if provider == 'TONGYI':
            # 通义千问特殊格式
            return {
                "model": model,
                "input": {
                    "messages": messages
                },
                "parameters": {
                    "result_format": "message",
                    "max_tokens": 50
                }
            }
        else:
            # OpenAI兼容格式
            return {
                "model": model,
                "messages": messages,
                "max_tokens": 50,
                "temperature": 0.1
            }

    @classmethod
    def _handle_response(cls, response: requests.Response, provider: str) -> Tuple[bool, str, Dict]:
        """处理API响应"""
        status_code = response.status_code

        if status_code == 200:
            try:
                data = response.json()
                # 提取回复内容
                content = cls._extract_content(data, provider)
                return True, f"连接成功！模型响应: {content[:50]}..." if len(content) > 50 else f"连接成功！模型响应: {content}", {
                    "success": True,
                    "provider": provider,
                    "status_code": status_code,
                    "response_preview": content[:100]
                }
            except json.JSONDecodeError:
                return False, "响应解析失败，返回的不是有效JSON", {
                    "error": "json_decode_error",
                    "provider": provider,
                    "status_code": status_code,
                    "raw_response": response.text[:200]
                }

        # 处理常见错误状态码
        error_messages = {
            401: "API Key 无效或已过期",
            403: "权限不足，请检查API Key权限",
            404: "API端点不存在，请检查URL",
            429: "请求过于频繁，请稍后再试",
            500: "服务器内部错误",
            502: "网关错误",
            503: "服务暂时不可用",
        }

        error_msg = error_messages.get(status_code, f"HTTP错误: {status_code}")

        # 尝试解析错误详情
        try:
            error_data = response.json()
            if 'error' in error_data:
                if isinstance(error_data['error'], dict):
                    detail = error_data['error'].get('message', '')
                    if detail:
                        error_msg += f" ({detail})"
                elif isinstance(error_data['error'], str):
                    error_msg += f" ({error_data['error']})"
        except:
            pass

        return False, error_msg, {
            "error": "http_error",
            "provider": provider,
            "status_code": status_code,
            "raw_response": response.text[:200]
        }

    @classmethod
    def _extract_content(cls, data: Dict, provider: str) -> str:
        """从响应中提取内容"""
        try:
            if provider == 'TONGYI':
                # 通义千问格式
                return data.get('output', {}).get('choices', [{}])[0].get('message', {}).get('content', '')
            else:
                # OpenAI兼容格式
                choices = data.get('choices', [])
                if choices:
                    message = choices[0].get('message', {})
                    return message.get('content', '')
            return str(data)[:100]
        except:
            return str(data)[:100]

    @classmethod
    def chat_completion(
        cls,
        provider: str,
        api_url: str,
        api_key: str,
        model: str,
        messages: list,
        timeout: int = 60
    ) -> Tuple[bool, str, str]:
        """
        多轮对话补全（供启动器内置 AI 助手使用）

        Args:
            provider: AI提供商代码
            api_url: API接口地址
            api_key: API密钥
            model: 模型名称
            messages: [{"role": "system|user|assistant", "content": "..."}]
            timeout: 请求超时时间(秒)

        Returns:
            Tuple[success: bool, message: str, reply: str]
        """
        if not api_key or not api_key.strip():
            return False, "API Key 不能为空", ""

        endpoint = cls._normalize_chat_endpoint(provider, api_url)
        if not endpoint:
            return False, "未提供有效的API地址", ""

        chat_model = model if model else cls.DEFAULT_MODELS.get(provider, 'gpt-3.5-turbo')

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

        clean_messages = [
            {
                "role": str(m.get("role", "user")),
                "content": str(m.get("content", "")),
            }
            for m in (messages or [])
            if isinstance(m, dict) and str(m.get("content", "")).strip()
        ]
        if not clean_messages:
            return False, "消息内容为空", ""

        if provider == 'TONGYI':
            payload = {
                "model": chat_model,
                "input": {"messages": clean_messages},
                "parameters": {
                    "result_format": "message",
                    "max_tokens": 1200
                }
            }
        else:
            payload = {
                "model": chat_model,
                "messages": clean_messages,
                "max_tokens": 1200,
                "temperature": 0.7
            }

        try:
            response = requests.post(
                endpoint,
                headers=headers,
                json=payload,
                timeout=timeout
            )
        except requests.exceptions.Timeout:
            return False, f"请求超时 ({timeout}秒)", ""
        except requests.exceptions.ConnectionError:
            return False, "网络连接失败，请检查网络", ""
        except requests.exceptions.RequestException as e:
            return False, f"请求异常: {str(e)}", ""
        except Exception as e:
            return False, f"对话失败: {str(e)}", ""

        if response.status_code == 200:
            try:
                content = cls._extract_content(response.json(), provider)
            except Exception:
                return False, "响应解析失败，返回的不是有效JSON", ""
            if not content or not str(content).strip():
                return False, "AI 未返回有效内容", ""
            return True, "OK", str(content)

        _, error_msg, _ = cls._handle_response(response, provider)
        return False, error_msg, ""

    @classmethod
    def quick_test(cls, provider: str, api_key: str) -> Tuple[bool, str]:
        """快速测试（使用默认配置）"""
        endpoint = cls.ENDPOINTS.get(provider)
        model = cls.DEFAULT_MODELS.get(provider)

        if provider == 'OTHER':
            return False, "自定义提供商需要提供完整的API地址和模型名称"

        success, msg, _ = cls.test_connectivity(provider, endpoint, api_key, model)
        return success, msg


    @classmethod
    def fetch_model_list(cls, provider: str, api_url: str, api_key: str, timeout: int = 30) -> Tuple[bool, str, list]:
        """
        获取AI提供商的模型列表

        Args:
            provider: AI提供商代码
            api_url: API接口地址
            api_key: API密钥
            timeout: 请求超时时间

        Returns:
            Tuple[success: bool, message: str, models: list]
        """
        if not api_key or not api_key.strip():
            return False, "API Key 不能为空", []

        if not api_url:
            return False, "API 地址不能为空", []

        # 构建模型列表端点
        # 标准 OpenAI 兼容接口通常是 /v1/models；若传入的是 /v1 或
        # /v1/chat/completions，避免拼成 /v1/v1/models。
        models_endpoint = cls._build_models_endpoint(api_url)

        headers = {
            'Authorization': f'Bearer {api_key}',
            'Content-Type': 'application/json',
        }

        try:
            response = requests.get(
                models_endpoint,
                headers=headers,
                timeout=timeout
            )

            if response.status_code == 200:
                data = response.json()
                models = data.get('data', [])

                # 标准化模型数据格式
                formatted_models = []
                for model in models:
                    if isinstance(model, dict):
                        formatted_models.append({
                            'id': model.get('id', ''),
                            'name': model.get('name', model.get('id', '')),
                            'description': model.get('description', '')
                        })
                    elif isinstance(model, str):
                        formatted_models.append({
                            'id': model,
                            'name': model,
                            'description': ''
                        })

                return True, f"成功获取 {len(formatted_models)} 个模型", formatted_models

            elif response.status_code == 404:
                # 某些提供商可能不支持 /models 端点
                return False, "该提供商不支持获取模型列表，请手动输入模型名称", []
            else:
                error_msg = f"请求失败: HTTP {response.status_code}"
                try:
                    error_data = response.json()
                    if 'error' in error_data:
                        if isinstance(error_data['error'], dict):
                            detail = error_data['error'].get('message', '')
                            if detail:
                                error_msg += f" ({detail})"
                except:
                    pass
                return False, error_msg, []

        except requests.exceptions.Timeout:
            return False, f"请求超时 ({timeout}秒)", []
        except requests.exceptions.ConnectionError:
            return False, "网络连接失败，请检查网络", []
        except Exception as e:
            return False, f"获取模型列表失败: {str(e)}", []


# 便捷函数
def test_ai_connectivity(provider: str, api_url: str, api_key: str, model: str, timeout: int = 30):
    """便捷函数：测试AI连通性"""
    return AIConnectivityTester.test_connectivity(provider, api_url, api_key, model, timeout)


def quick_test_ai(provider: str, api_key: str):
    """便捷函数：快速测试AI"""
    return AIConnectivityTester.quick_test(provider, api_key)


def fetch_model_list(provider: str, api_url: str, api_key: str, timeout: int = 30):
    """便捷函数：获取模型列表"""
    return AIConnectivityTester.fetch_model_list(provider, api_url, api_key, timeout)


if __name__ == "__main__":
    # 测试示例
    print("AI连通性测试工具")
    print("-" * 50)

    # 测试用例
    test_cases = [
        ("SILICON", "https://api.siliconflow.cn/v1/chat/completions", "sk-test-key", "Qwen/Qwen2.5-7B-Instruct"),
    ]

    for provider, url, key, model in test_cases:
        print(f"\n测试 {AIConnectivityTester.PLATFORM_NAMES.get(provider, provider)}...")
        success, msg, details = AIConnectivityTester.test_connectivity(provider, url, key, model, timeout=10)
        print(f"结果: {'✓ 成功' if success else '✗ 失败'}")
        print(f"消息: {msg}")
        print(f"详情: {details}")
