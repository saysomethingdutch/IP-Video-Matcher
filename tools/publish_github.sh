#!/bin/zsh
# 把主仓库的 main 和标签同步到 GitHub 私有仓库（DECISIONS D-083）。
#
# 主仓库的历史里有客户文件（比对要求、比对表副本）和论文 PDF，所以主仓库不设远程、不直接 push。
# 本脚本每次都从主仓库重新克隆，把这些路径从历史里清掉，自查通过后再推送。
# 清理是确定性的：已经推送过的提交，重做一遍编号不变，所以是普通的追加推送，不需要强推。
#
#   tools/publish_github.sh          预览：列出将要推送的新提交，不推送
#   tools/publish_github.sh --yes    推送
set -e
set -o pipefail

src=${0:A:h:h}
remote=https://github.com/saysomethingdutch/IP-Video-Matcher.git
remove=(IP-Video-Matcher-Architecture-Battle-Pack.zip claude_architecture_battle_pack papers)
secrets='sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|[0-9a-f]{32}\.[A-Za-z0-9]{16}|eyJ[A-Za-z0-9_-]{30,}\.[A-Za-z0-9_-]{20,}|SESSDATA=[^; ]{8,}'

work=$(mktemp -d "${TMPDIR:-/tmp}/ipvm-publish.XXXXXX")
trap 'rm -rf "$work"' EXIT

git clone -q --no-hardlinks "$src" "$work/repo"
cd "$work/repo"
git remote remove origin
FILTER_BRANCH_SQUELCH_WARNING=1 git filter-branch -f \
  --index-filter "git rm -r -q --cached --ignore-unmatch ${remove[*]}" \
  --prune-empty --tag-name-filter cat -- main --tags >/dev/null 2>&1

# 自查一：要清掉的路径不能再出现在历史里
if git log main --pretty=format: --name-only | grep -qE '^papers/|Battle-Pack\.zip|^claude_architecture_battle_pack/'; then
  echo "停止：客户文件或论文没有清干净。"; exit 1
fi
# 自查二：历史里不能有文档、压缩包这类文件（防止以后又有客户材料被误提交）
docs=$(git rev-list --objects main | grep -iE '\.(docx?|xlsx?|pdf|zip|pptx?)$' || true)
if [ -n "$docs" ]; then
  echo "停止：历史里有文档或压缩包，先确认是不是客户材料："; echo "$docs" | awk '{print "  " $2}' | sort -u | head -20; exit 1
fi

git remote add origin "$remote"
git fetch -q origin main 2>/dev/null || true
if git rev-parse -q --verify origin/main >/dev/null; then
  if ! git merge-base --is-ancestor origin/main main; then
    echo "停止：GitHub 上的 main 不是本次结果的祖先，不能追加推送。先查清原因，不要强推。"; exit 1
  fi
  range="origin/main..main"
else
  range="main"
fi

# 自查三：新提交里不能有密钥
if git log -p "$range" | grep -qE "^\+.*($secrets)"; then
  echo "停止：新提交里有像密钥的内容。"; exit 1
fi

count=$(git rev-list --count "$range")
echo "将推送 $count 个新提交："
git log --oneline "$range" | head -20

if [ "$1" != "--yes" ]; then
  echo "这次只是预览，没有推送。确认后运行： tools/publish_github.sh --yes"
  exit 0
fi
git push -q origin main
git push -q origin --tags
echo "已推送。GitHub main：$(git ls-remote origin refs/heads/main | cut -c1-7)"
