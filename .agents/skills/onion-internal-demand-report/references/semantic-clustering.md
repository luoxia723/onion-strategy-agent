# 内部需求语义归并合同

## 1. 为什么需要分批模型归并

AI-02 已经看懂单条素材，但具体问题通常是逐素材独立措辞。精确文本重复、关键词类别、产品名称和广告主要面向人群都不足以决定跨素材需求是否相同。

正式归并由模型完成；确定性脚本只负责分批和数量守恒。

## 2. 标准需求单元

每行 JSON 至少包含：

~~~json
{
  "unit_id": "N-000001",
  "business_line": "app",
  "material_id": "稳定素材ID",
  "demand_subject": "student",
  "audience_description": "……",
  "task_scene": "……",
  "specific_problem": "……",
  "current_coping": null,
  "desired_change": "……",
  "evidence_ids": ["S2"],
  "is_high_performance": true,
  "material_title": "……",
  "dashboard_path": "https://toufang-ai.guanghexinzhi.cn/content-dashboard?...",
  "material_carrying": {
    "promoted_offering": "……",
    "persuasion_path_summary": "……",
    "persuasion_steps": []
  }
}
~~~

需求主体必须根据具体场景和问题重判。广告喊给家长但问题发生在孩子做题、听课或复习时，需求主体仍可以是 student；家长不会辅导、无法规划、时间或情绪负担才是 parent。

## 3. 粗分桶

运行：

~~~bash
python3 scripts/prepare_semantic_batches.py 需求单元.jsonl --output-dir 语义批次
~~~

脚本默认按业务线、需求主体和粗任务拆分，并同时限制每批不超过80条、紧凑JSON不超过120,000字节；模型请求文件也使用紧凑JSON，避免单批虽然条数合规但上下文被格式空白放大。粗任务可以使用关键词，但只用于控制上下文；批次名称不是正式需求类别，不能写入报告。

当执行模型已经核实具有足够大的上下文和结构化输出容量时，正式运行优先使用大上下文分区模式：只按业务线和初始需求主体隔离，单批约300条、紧凑JSON不超过800,000字节。模型仍可在批内修正需求主体，运行器仍会按修正后的业务线＋主体执行跨批归并；该模式不改变需求定义和报告格式，只减少人为粗任务边界和模型调用次数：

~~~bash
python3 scripts/prepare_semantic_batches.py 需求单元.jsonl \
  --output-dir 语义批次 \
  --grouping-mode subject \
  --max-batch-size 300 \
  --max-batch-bytes 800000
~~~

紧接着初始化可续跑语义任务：

~~~bash
python3 scripts/run_semantic_clustering.py init \
  需求单元.jsonl 语义批次 \
  --run-dir 语义运行
python3 scripts/run_semantic_clustering.py status --run-dir 语义运行
~~~

`init`会冻结需求单元和批次manifest摘要，并生成所有批内模型请求。只有批次文件而后续任务没有完成时，状态必须保持`batch_pending`，不得生成正式报告。

## 4. 批内模型归并

模型逐批读取完整需求单元，按以下四项判断是否同一需求：

1. 需求主体相同；
2. 正在完成的学习或家庭任务相同；
3. 卡住的核心问题机制相同；
4. 想先发生的变化相同或兼容。

必须拆分：

- 学生学习卡点与家长辅导/规划/时间负担；
- 无法开始与开始后不能坚持；
- 没听懂与听懂但不会迁移；
- 不知道先做什么与知道顺序但执行不下去；
- 只共享“错题、规划、提分、焦虑”等宽词；
- 产品相同但问题不同。

每个批内候选簇输出：

- 临时候选簇 ID；
- 标准需求名称；
- 主体、任务场景、核心问题和期待变化定义；
- 成员需求单元 ID；
- 1至3个中心样本 ID；
- 最容易与相邻需求混淆的边界样本 ID；
- 合并理由和禁止继续合并的边界。

单条需求可以形成单例簇，不强迫合并。每个输入需求单元必须且只能进入一个批内候选簇。

模型还要重新判断需求主体。批内响应可以把误判为student的家长辅导、规划、时间或情绪负担修正为parent，或反向修正；运行器会据此生成`normalized_units.jsonl`。业务线不能修改。

查看下一个任务：

~~~bash
python3 scripts/run_semantic_clustering.py next --run-dir 语义运行
~~~

当前Agent人工完成一个请求后，把纯JSON响应写入文件并验收：

~~~bash
python3 scripts/run_semantic_clustering.py accept \
  --run-dir 语义运行 \
  --job-id batch-001 \
  --response batch-001.response.json
~~~

如已配置经过授权的模型命令，可以断点续跑。每次调用数量由`--max-jobs`限制；命令会消费外部模型时，必须在当前任务明确获得授权后才能传`--approved-model-run`：

~~~bash
python3 scripts/run_semantic_clustering.py run \
  --run-dir 语义运行 \
  --model-command '模型适配命令' \
  --max-jobs 1 \
  --approved-model-run
~~~

在Codex任务中由当前Codex自身执行时，使用内置适配器；它为每个任务启动只读、临时、结构化输出的同模型执行，并把纯JSON交回同一守恒门禁：

~~~bash
python3 scripts/run_semantic_clustering.py run \
  --run-dir 语义运行 \
  --model-command 'python3 scripts/codex_semantic_model_adapter.py' \
  --max-jobs 3 \
  --parallel-jobs 3 \
  --timeout-seconds 900 \
  --approved-model-run
~~~

默认一次只跑一个任务。正式运行确认模型并发能力后，可使用`--parallel-jobs 2`至`4`并发生成不同任务；每一波开始前只由主进程记录尝试次数，模型输出完成后仍由主进程按固定任务顺序逐个验收和写入状态，避免并发改写断点。任一任务失败时，其他已经通过的任务仍保留，失败任务保持pending并可单独续跑。

运行器把一个模型请求以JSON写入命令stdin，并只接受stdout中的纯JSON。每个任务先校验成员、主体、问题表达组、当前应对组、中心样本和边界样本，再写入断点；错误响应保留在attempts中并停止，不会跳过后继续拼报告。

## 5. 跨批合并

批内完成后，不再把全部需求单元原文一次交给模型。只提交候选簇定义、中心样本和边界样本，按同样四项规则检查：

- 不同批次是否形成同一需求；
- 相同名称是否实际是不同问题；
- 粗任务分桶是否错误隔开同义需求；
- 家长与学生需求是否被错误混合；
- APP 与线索分别合并，不能跨业务线形成同一正式簇。
- student、parent 与 other 不能进入同一正式簇；主体重判错误应先修正需求单元，不在归并阶段混合弥补。

最终映射格式：

~~~json
{
  "schema_version": "internal_demand_cluster_mapping_v2",
  "clusters": [
    {
      "cluster_id": "APP-C001",
      "business_line": "app",
      "canonical_name": "做题找不到第一步",
      "demand_subject": "student",
      "task_scene": "独立做题准备下笔时",
      "core_problem": "无法识别解题入口",
      "desired_change": "先确定第一步",
      "member_unit_ids": ["N-000001"],
      "problem_expression_groups": [
        {
          "label": "看不出第一步",
          "member_unit_ids": ["N-000001"]
        }
      ],
      "current_coping_groups": [
        {
          "label": "直接看答案",
          "member_unit_ids": ["N-000001"]
        }
      ],
      "center_unit_ids": ["N-000001"],
      "boundary_unit_ids": [],
      "merge_reason": "……",
      "split_boundary": "与做到中间无法继续拆分"
    }
  ]
}
~~~

`problem_expression_groups` 必须将每个成员需求单元恰好分到1至4组，用来生成“内部素材主要写了哪些具体问题”。这些组只能拆解同一核心问题的具体表现；如果必须用两个不同核心问题才能命名，应先拆开正式需求簇。

`current_coping_groups` 只覆盖 `current_coping` 非空的成员单元，同样分成1至4组。没有任何明确当前应对时输出空列表，报告如实说明，不自行推演。

全部批内任务通过后，运行器会自动按业务线＋修正后的需求主体生成`cross-app-student`等跨批任务。所有跨批任务通过后才生成：

- `normalized_units.jsonl`：包含主体修正后的全部需求单元；
- `final_mapping.json`：正式`internal_demand_cluster_mapping_v2`映射；
- `receipt.json`：单元数、批次数、跨批任务数、最终簇数、输入/映射摘要与校验状态；
- `state.json`：状态必须为`complete`。

关键词分类、粗分桶、旧报告卡或只有批内响应都不能作为`final_mapping.json`的替代品。

## 6. 确定性守恒

运行：

~~~bash
python3 scripts/validate_semantic_mapping.py 需求单元.jsonl 最终归并映射.json
~~~

正式运行使用主体修正后的文件：

~~~bash
python3 scripts/validate_semantic_mapping.py \
  语义运行/normalized_units.jsonl \
  语义运行/final_mapping.json
~~~

脚本阻断：

- 需求单元遗漏或重复归属；
- APP 与线索跨业务线混合；
- 需求主体跨 student、parent 或 other 混合；
- 中心/边界样本不属于成员；
- 空标准名称、空场景、空核心问题或空期待变化；
- 重复正式簇 ID。
- 具体问题表达组漏掉或重复分配成员单元；
- 当前应对组漏掉、重复分配或错误包含应对为空的单元；

脚本不判断两条需求语义是否真的相同。语义质量由模型中心/边界复核和人工抽查负责。

## 7. 正式报告门禁

只有以下条件同时成立才生成正式报告：

1. `state.json.status=complete`；
2. `receipt.json.validation_status=passed`；
3. `receipt.json.final_mapping_sha256`等于`final_mapping.json`真实摘要；
4. 确定性映射校验通过；
5. 报告由`build_report_from_mapping.py`消费最终映射生成；
6. 报告头写入映射摘要和回执路径；
7. `validate_report.py`回读回执并通过。

缺少任一条件时返回`internal_semantic_mapping_incomplete`。试验稿可以显式使用`--allow-trial-without-semantic-mapping`检查格式，但必须写明不能进入购买动因，并且不能改名成正式报告。

## 8. 功能/服务承接

最终需求簇确定后，在簇内按稳定素材 ID 去重，并为每条成员素材确定一个主要功能/服务承接。

判断可以使用 promoted_offering 及必需的 function 步骤，但报告只展示与正式《产品事实与卖点》唯一对应后的标准名。不得直接使用 promoted_offering 原字符串作为分组键；品牌前缀、AI 前缀、“功能/服务”后缀、大小写、空格、括号和明显误写不能制造新对象。

无法唯一对应正式产品事实时，在数据层保留未归一状态，不写入报告的功能/服务承接字段。不展开历史原名、说服路径、功能动作、利益、结果、证明、权益或 CTA。

同一需求下各功能/服务的全部素材数之和必须等于该需求的全部素材数，高表现数之和必须等于该需求的高表现素材数。
