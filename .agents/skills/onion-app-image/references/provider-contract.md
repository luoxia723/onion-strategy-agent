# KIE 统一 MCP 生图合同

正式图片生成只调统一 `onion-agent` OAuth MCP 的`generation_kie_image`。KIE Key、供应商网址、参考图暂存、异步任务轮询和结果下载均在服务端封装；下游项目不配置`KIE_API_KEY`、`KIE_BASE_URL`或`KIE_UPLOAD_BASE_URL`。

## 工具映射

- `prompt`：`image-render-manifest.json` 对应job的完整Prompt；
- `aspect_ratio`：由已保存版位配置的`size`确定性映射，不从Prompt自由推测；
- `reference_output_ids`：正常图生图路线优先使用。参考图先通过`generation_upload_media`上传一次，各job只按Logo、IP、风格/构图、字体、真实UI的已确认顺序传返回的`output_id`，单次最多8张；
- `reference_images`：仅作内联兼容入口；与`reference_output_ids`不能同时使用。每张必须显式包含`file_name`、`mime_type`和不带`data:`URL前缀的`base64_data`，不接受本地路径、URL或其他字段名；单张不超过10MB，总计不超过64MB；
- `approved_in_current_task`：只在用户当前明确要求付费生图且候选数已确认时传`true`；
- `idempotency_key`：稳定绑定`task_id＋job_id＋清单指纹`。

当前服务端固定使用GPT Image 2：无参考图时是文生图，有参考图时是图生图，分辨率固定`2K`。参考图只用于配置卡明确声明的Logo、IP、字体、UI、风格或构图角色，不用于已有广告图迭代。

## 执行与恢复

1. 先运行`render.py --validate-only`，校验清单、Prompt、比例、参考图顺序和输出路径；该步不调用MCP生成工具。
2. 明确预计生成数并取得当前付费确认。
3. 有参考图时先按资产SHA-256去重，每个唯一资产调一次`generation_upload_media`并保留`output_id`；不要在两个job里重复传同一段base64。
4. 逐job调`generation_kie_image`，优先传`reference_output_ids`。服务端先检查当前用户对参考产物的归属、过期、Prompt、比例和图片字节，通过后才登记付费幂等操作并请求KIE；网络超时或响应不确定时使用该job的同一幂等Key回查，不创建新Key。
5. 成功响应必须包含`output_id`、`mime_type`、`byte_count`、`sha256`、供应商`task_id`和短时下载地址。立即下载到清单输出路径并核对哈希；短时地址不进入长期交付。
6. 原请求已失败时停止并保留错误状态。只有用户明确修改Prompt、参考图或候选意图并同意新生成，才建立新版本Key。

版位要求精确宽高或体积上限时，将KIE返回的`output_id`传给`generation_prepare_image_delivery`；工具在服务端完成cover居中裁切、JPEG转换和逐级压缩，再返回可校验的新产物。下游不安装Pillow。

供应商生成成功、尺寸合格和哈希一致都不替代图片文字、UI、品牌和内容的人工审核。
