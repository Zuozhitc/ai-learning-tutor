# 检索诊断报告

这是同一作者编写的小型诊断集，不是独立盲测或教学效果验证。
未调用生成模型；下列分数只描述教材卡召回。增强规则未根据本次分数反复调参。

教材卡：11；问题：17。

| 方式 | Hit@1 | Hit@3 | MRR@3 | 关联卡 Hit@3 | 无支持问题仍有候选 | 平均检索 ms |
| --- | --- | --- | --- | --- | --- | --- |
| bm25 | 92.3% | 92.3% | 0.923 | 100.0% | 0/4 | 0.047 |
| concept-bm25 | 92.3% | 100.0% | 0.962 | 100.0% | 2/4 | 0.071 |

Hit@k 指人工标注的可接受教材卡至少有一张出现在前 k 个候选，不能据此推断完整回答正确。
无支持问题即使有候选卡也不代表可回答；词法方法尤其无法可靠判断语义覆盖范围。

## 逐题结果

### bm25

| 问题 | 期望卡 | 实际前3卡 | 首个相关排名 |
| --- | --- | --- | --- |
| 一个运营需求，怎么翻译成机器学习任务？ | features_and_target, task_goal | task_goal, features_and_target, classification_vs_clustering | 1 |
| 根据历史标签预测客户未来一个月是否购买 | classification, features_and_target, task_goal | classification, regression, classification_vs_clustering | 1 |
| 想估计下一月销售额，目标是金额，不是买不买 | regression, task_goal | regression, probability_vs_regression, probability_to_action | 1 |
| 没有现成客群标签，想发现行为相似的客户群 | classification_vs_clustering, clustering | classification_vs_clustering, clustering, task_goal | 1 |
| 分类输出0.7的概率，为什么不叫回归？ | probability_vs_regression | probability_vs_regression, classification, classification_metrics | 1 |
| 分类和聚类都把客户分组，区别到底在哪？ | classification_vs_clustering | classification_vs_clustering, clustering, cluster_id | 1 |
| 已有购买概率，为什么行动还要定一个阈值？ | probability_to_action | probability_to_action, cluster_id, probability_vs_regression | 1 |
| 买的可能性有七成，这个数怎么拿来做行动规则？ | probability_to_action | probability_to_action | 1 |
| 想预估他下个月花多少钱，这要什么任务？ | regression, task_goal | 无命中 | — |
| precision和recall分别看误报还是漏报？ | classification_metrics, imbalanced_accuracy | imbalanced_accuracy, classification_metrics | 1 |
| 欺诈很少时全判正常也有99%准确率，为什么不行？ | imbalanced_accuracy | imbalanced_accuracy, classification_metrics | 1 |
| 聚类的组号2表示比组号1更有价值吗？ | cluster_id | cluster_id, imbalanced_accuracy, classification | 1 |
| 预测时能用哪些输入特征，X和Y怎么区分？ | features_and_target | features_and_target, cluster_id, classification | 1 |
| 明天深圳会下雨吗？ | 库中不支持 | 无命中 | — |
| Transformer注意力矩阵的维度怎么推导？ | 库中不支持 | 无命中 | — |
| 概率论的贝叶斯公式怎么证明？ | 库中不支持 | 无命中 | — |
| 聚类算法的收敛性证明是什么？ | 库中不支持 | 无命中 | — |

### concept-bm25

| 问题 | 期望卡 | 实际前3卡 | 首个相关排名 |
| --- | --- | --- | --- |
| 一个运营需求，怎么翻译成机器学习任务？ | features_and_target, task_goal | task_goal, features_and_target, regression | 1 |
| 根据历史标签预测客户未来一个月是否购买 | classification, features_and_target, task_goal | classification, classification_vs_clustering, features_and_target | 1 |
| 想估计下一月销售额，目标是金额，不是买不买 | regression, task_goal | regression, probability_vs_regression, task_goal | 1 |
| 没有现成客群标签，想发现行为相似的客户群 | classification_vs_clustering, clustering | classification_vs_clustering, clustering, task_goal | 1 |
| 分类输出0.7的概率，为什么不叫回归？ | probability_vs_regression | probability_vs_regression, classification, classification_metrics | 1 |
| 分类和聚类都把客户分组，区别到底在哪？ | classification_vs_clustering | classification_vs_clustering, clustering, task_goal | 1 |
| 已有购买概率，为什么行动还要定一个阈值？ | probability_to_action | probability_to_action, probability_vs_regression, cluster_id | 1 |
| 买的可能性有七成，这个数怎么拿来做行动规则？ | probability_to_action | probability_to_action | 1 |
| 想预估他下个月花多少钱，这要什么任务？ | regression, task_goal | probability_vs_regression, task_goal, regression | 2 |
| precision和recall分别看误报还是漏报？ | classification_metrics, imbalanced_accuracy | imbalanced_accuracy, classification_metrics | 1 |
| 欺诈很少时全判正常也有99%准确率，为什么不行？ | imbalanced_accuracy | imbalanced_accuracy, classification_metrics | 1 |
| 聚类的组号2表示比组号1更有价值吗？ | cluster_id | cluster_id, imbalanced_accuracy, classification | 1 |
| 预测时能用哪些输入特征，X和Y怎么区分？ | features_and_target | features_and_target, regression, task_goal | 1 |
| 明天深圳会下雨吗？ | 库中不支持 | 无命中 | — |
| Transformer注意力矩阵的维度怎么推导？ | 库中不支持 | 无命中 | — |
| 概率论的贝叶斯公式怎么证明？ | 库中不支持 | probability_to_action, probability_vs_regression, classification_metrics | — |
| 聚类算法的收敛性证明是什么？ | 库中不支持 | cluster_id, clustering, classification_vs_clustering | — |

## 限制

- 当前中文分词采用字符二元组，概念增强依赖人工维护的别名表。
- 无向量模型、向量数据库或大模型调用；未验证8B生成、引用忠实度与真实教学效果。
- 无支持的问题与课程词汇重叠时仍会召回材料；需要生成端的依据检查及更多真实问题验证。
- 下一步增加其他学习者的问题和人工相关性标注，再与真实嵌入模型检索做对比。
