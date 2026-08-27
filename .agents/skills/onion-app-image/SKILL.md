---
name: onion-app-image
description: 当用户已经确认 APP 图片文案并要求生成信息流、应用商店或学习机正式图片时使用；打开本地配置卡，通过统一OAuth MCP的KIE GPT Image 2生成候选，完成品牌与UI门禁、规格处理、人工选择和采纳图片交付。不要用于撰写图片文案、修改已有广告图、线索九宫格或投放发布。
---

# APP 图片生成

> 业务状态：2026-08-26按旧正式Skill直接复用并完成当前仓库迁移。配置卡、102条版位规则、KIE异步任务与断点恢复、批量渲染、Logo/IP/字体资产、规格处理、候选选择和采纳打包均已恢复；除非xhh明确重新开启讨论，不重做业务流程。具体付费生图仍按当前任务逐次授权。

> 执行适配：业务流程不变；KIE Key、参考图上传、异步任务和结果暂存已收口到统一OAuth MCP，下游不再配置供应商Key。

## 产物

把已确认图片文案转成可选择、可追溯的图片候选，并只把用户采纳的图片作为正式交付。当前只做“确认文案 → 配置卡 → KIE直接生图”，不做已有图片编辑或同款复刻。

## 运行前读取

- [统一 OAuth MCP 适配](../../references/http-mcp-adapter.md)
- [输入、输出与人工门禁](references/input-output-contract.md)
- [渠道与版位](references/channel-placement.md)
- [视觉与Prompt规则](references/prompt-rules.md)
- [合规视觉雷区](references/visual-compliance.md)
- [KIE生图合同](references/provider-contract.md)
- [压缩与导出](references/export-rules.md)
- [任务合同](references/task-contract.md)
- [渲染清单合同](references/render-manifest.md)

## 必需输入

- 已确认的 APP 图片文案；
- 产品事实与品牌资产；
- 渠道、版位、图片形式、套数、Logo/IP/字体/CTA/UI等制作配置；
- 需要可识别 UI 时的真实产品截图；
- 用户对候选数量和付费生成的当前确认。

正式生图调用统一 `onion-agent` OAuth MCP 的`generation_kie_image`。下游项目不读取、不保存也不要求用户填写KIE Key。

## 固定流程

1. 确认输入是已批准的固定格式APP图片文案；把每套的渠道、图片形式、主标题/副标题或短句整理为配置卡上下文。
2. 建立“产品事实→目标人群→具体场景→产品动作→可感知变化”的最小视觉链。
3. 启动`scripts/interactive_server.py`并打开返回的本地配置卡。即使聊天中已经说过参数，也必须由用户保存配置结果。
4. 需要可识别APP/UI时等待用户上传真实截图；缺失时停止或改为不可识别的抽象屏幕。
5. 根据配置结果与[Prompt规则](references/prompt-rules.md)生成`image-render-manifest.json`；逐张写清文案、场景、产品动作、构图、参考资产、安全区和禁止项。
6. 运行`scripts/validate_task.py`和每个任务的`scripts/render.py --validate-only`，只验证清单、Prompt、比例、引用顺序和输出路径，不调用付费接口。
7. 告知预计图组数和图片数；只有取得当前任务付费确认后，才按`image-render-manifest.json`的job顺序调用`generation_kie_image`。将Prompt、版位比例和最多8张已确认参考图（base64、MIME、文件名，总计不超过64MB）传给工具，传`approved_in_current_task=true`，幂等Key固定绑定`task_id＋job_id＋清单指纹`。网络重试复用原Key；失败后不自动换Key重新创建付费任务。
8. 将工具返回的候选图立即下载到清单的`output`路径，校验`sha256`和MIME，并在`image-render-result.json`保留`job_id`、`output_id`、哈希、供应商taskId和人工状态；不保存短时URL作为长期引用。版位要求精确尺寸或体积时，将KIE `output_id`、目标宽高和KB上限传给`generation_prepare_image_delivery`，下载并校验返回JPEG；下游不需要安装Pillow。然后运行`scripts/build_selection_page.py`生成本地选择页。
9. 用户逐套选择；只有采纳图片进入交付目录。用户明确要求打包时才运行`scripts/package_accepted_images.py`。
10. 默认写入`.runtime/策略Agent产物/<北京时间日期>/APP图片/<任务ID>/`，不上传广告平台、不发布。

## 核心边界

- 文案必须逐字使用确认稿，生图模型不能改写产品事实或 CTA。
- 没有真实 UI 截图时，不生成可识别的虚构界面。
- 参考图必须标明 Logo、IP、字体、UI、风格或构图角色；不把旧广告图当编辑基准。
- 多套图片可以换场景、人物、构图和视觉隐喻，不能改变文案核心与功能事实。
- 付费生成、外部上传和写对象存储需要当前任务授权。
- 旧任务文件里的`paid_generation_approved=true`不能替代当前任务命令上的`--approved-in-current-task`。
- KIE提交结果不确定时保留任务状态并停止，不能再次创建可能重复扣费的任务。
- 下游不执行`render.py`或`batch_render.py`的直连KIE付费分支；这两个脚本只保留确定性清单校验与本地后处理能力。
- 交付打包只复用已经服务端处理并校验的JPEG，运行`scripts/package_accepted_images.py`时不再开启本地压缩分支。
- 自动规格检查通过后仍需要人工查看文字和内容。
