# AI API Payload 格式说明

## 概述
发送给AI服务的payload包含邮件内容和附件URL信息，让AI能够处理包含图片的邮件。

## Payload 结构

### 完整格式
```json
{
  "message_id": "email_uuid_or_message_id",
  "text": "清理后的邮件正文内容",
  "attachments": [
    {
      "url": "https://storage.example.com/file1.jpg",
      "filename": "screenshot.jpg", 
      "type": "image/jpeg",
      "size": 1024000
    }
  ],
  "has_attachments": true
}
```

### 字段说明

#### 必填字段
- **`message_id`**: 邮件唯一标识符
- **`text`**: 经过清理的邮件正文内容
- **`attachments`**: 附件信息数组
- **`has_attachments`**: 是否包含附件的布尔值

#### 附件对象字段
- **`url`**: 附件访问URL
- **`filename`**: 文件名
- **`type`**: MIME类型
- **`size`**: 文件大小（字节）

## 使用场景

### 1. 纯文本邮件
```json
{
  "message_id": "uuid-123",
  "text": "你好，我想咨询产品价格。",
  "attachments": [],
  "has_attachments": false
}
```

### 2. 包含图片附件的邮件
```json
{
  "message_id": "uuid-456", 
  "text": "请看附件中的产品图片，我想了解详细信息。",
  "attachments": [
    {
      "url": "https://storage.example.com/images/product1.jpg",
      "filename": "product1.jpg",
      "type": "image/jpeg", 
      "size": 2048000
    }
  ],
  "has_attachments": true
}
```

### 3. 多个附件
```json
{
  "message_id": "uuid-789",
  "text": "附件中是相关文档，请查看。", 
  "attachments": [
    {
      "url": "https://storage.example.com/doc1.jpg",
      "filename": "合同扫描件.jpg",
      "type": "image/jpeg",
      "size": 1500000
    },
    {
      "url": "https://storage.example.com/doc2.png", 
      "filename": "身份证.png",
      "type": "image/png",
      "size": 800000
    }
  ],
  "has_attachments": true
}
```

## AI 服务端处理建议

### 1. 附件处理
```python
def process_email_with_attachments(payload):
    text = payload.get('text', '')
    attachments = payload.get('attachments', [])
    
    if attachments:
        # 下载并分析附件内容
        for attachment in attachments:
            if attachment['type'].startswith('image/'):
                # 处理图片附件
                image_analysis = analyze_image(attachment['url'])
                text += f"\n[图片内容: {image_analysis}]"
    
    return generate_response(text)
```

### 2. 上下文增强
```python
def enhance_context(payload):
    context = []
    
    # 添加附件信息
    if payload.get('has_attachments'):
        attachment_count = len(payload.get('attachments', []))
        context.append(f"包含 {attachment_count} 个附件")
    
    return "\n".join(context) + "\n\n" + payload['text'] if context else payload['text']
```

## 错误处理

### 附件信息解析失败
```python
try:
    attachment_info = json.loads(email_data.get('attachment_info', '[]'))
except json.JSONDecodeError:
    logger.warning("Failed to parse attachment info, using empty list")
    attachment_info = []
```

### 缺失字段处理
```python
def safe_get_payload(email_data):
    return {
        "message_id": email_data.get("message_id") or f"email_{email_data.get('email_uuid', 'unknown')}",
        "text": email_data.get('content', ''),
        "attachments": parse_attachments(email_data),
        "has_attachments": bool(email_data.get('has_attachment', False))
    }
```

## 配置说明

### 环境变量
- **`AI_API_URL`**: AI服务API地址
- **`AI_API_KEY`**: API认证密钥
- **`AI_API_TIMEOUT`**: 请求超时时间（默认15秒）

### 日志输出
```
INFO - Found 2 attachment(s) to send to AI
DEBUG - Sending payload with attachments: [{'url': '...', 'filename': '...'}]
```

这种增强的payload格式让AI能够获得完整的邮件上下文信息，包括图片附件URL，从而提供更准确和相关的回复！