# Git 协作规范
main保持可构建。每个短分支只做一个可验收任务；建议codex/b/T06-workflow（自动化协作）或团队约定feature/b/T06-workflow。不维护六条长期个人分支。
提交采用Conventional Commits并用中文说明，如 feat(spec): 增加规格版本保存、docs(architecture): 区分规划与执行。
提交只添加明确文件；不提交.env、数据库、缓存、node_modules、.tmp或用户上传文件。真实操作者身份提交，不伪造队友贡献。

## PR
描述包括：解决的问题、用户行为变化、需求/任务编号、契约或迁移影响、验证命令与结果、截图（UI）、未验证边界。
至少一名非作者Review；Schema/API由A与消费者共同评审，数据库由E评审，图规则由D评审。
合并前构建与相关测试通过，使用squash保持一个可回滚单元。数据库改动包含迁移与回退/备份说明。
冲突先同步main，不能以覆盖他人改动消除冲突。共享文件指定owner，通过小PR修改。

## 3–5人兼任与现有六人
六人仍为A架构/契约、B前端、C PM、D编排、E后端数据、F测试。
5人合并A/F，4人合并A/E与D/F，3人分为A/D/E、B、C/F；每个模块仍有唯一owner，跨模块Review不可省略。
设计文档的维护职责见[团队分工](TEAM_ASSIGNMENTS.md)。
