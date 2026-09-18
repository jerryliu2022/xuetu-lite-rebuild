# -*- coding: utf-8 -*-
"""
learn 模块：一个可交互的「边跑边学」子系统。

设计原则（重要）：
1. 本模块只读地复用主项目的数据库和模型文件，绝不修改主项目任何现有代码或数据。
2. 所有练习（SQL、Python 后端代码）都跑在 learn/data/learn_test.db 这份「练习副本」上，
   正式库 data/processed/xuetu_lite.db 始终是干净且只读的。
3. 整个 learn 只占一个目录 learn/，不污染主项目结构。
"""
