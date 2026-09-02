# 问题

Architecture Baseline v0.1 同时要求“Phase 1 Video Matching MVP”以及多层视觉索引（SigLIP/DINOv2 + FAISS）。后者会显著增加模型下载、运行时和 Apple Silicon 兼容性工作，其主要价值是 Phase 2 的变换鲁棒性。

# 当前方案

Phase 1 使用统一 `ImageEncoder` 接口下的低频 DCT 视觉描述符，候选召回采用 NumPy 精确余弦检索，时间轴使用 offset 聚类与 RANSAC 线性拟合。接口、缓存和 SQLite 数据结构允许后续替换编码器与向量索引。

# 为什么当前方案存在问题

该方案足以验证直接剪切、重编码和多集拼接的端到端数据流，但不应被视为 Architecture Baseline 中 Layer 2（视觉 embedding + FAISS）的最终实现。长剧集规模下，精确检索的时间和内存复杂度也高于 ANN 索引。

# 方案A

Phase 2 采用 DINOv2 embedding + FAISS，保留当前 DCT 特征作为 Layer 1 快速候选和交叉验证。

# 方案B

Phase 2 采用 SigLIP embedding + FAISS，保留当前 DCT 特征作为 Layer 1。SigLIP 更偏语义相似，可能增加同场景误匹配，需要更严格的时序验证。

# 推荐方案

在 5–10 条真实 golden set 上分别做小样本技术评估后再决定。若优先局部视觉结构与镜头重用定位，初步推荐 DINOv2；如果后续语义召回复用价值更高，再选择 SigLIP。未经架构负责人确认，不在当前 Phase 1 引入任何一个。

# 性能影响

FAISS 可降低长剧集候选检索成本；embedding 推理会提高建库时间、内存和磁盘占用。当前 DCT 基线在小型 synthetic benchmark 上用于测通，不代表整季性能。

# 数据兼容影响

需要新增 encoder/version 维度的索引缓存，但不改变 `segment_matches` schema。旧索引可按 `algorithm_version` 并存或重建。

# 对后续模块影响

只影响 Original Indexer 与 Candidate Retrieval 的实现，不改变 Temporal Alignment、审计 JSON、SQLite 结果表或未来语义层接口。
