#!/usr/bin/env bash
set -euo pipefail

base_dir="${1:?missing base dir}"
release_id="${2:?missing release id}"
keep_releases="${3:-5}"

releases_dir="${base_dir}/releases"
release_dir="${releases_dir}/${release_id}"
current_link="${base_dir}/current"
next_link="${base_dir}/current.next"

if [[ ! -d "${release_dir}" ]]; then
  echo "release dir not found: ${release_dir}" >&2
  exit 1
fi

if [[ ! -f "${release_dir}/index.html" ]]; then
  echo "release index.html missing: ${release_dir}/index.html" >&2
  exit 1
fi

# 先创建新的软链接，再用 mv -T 覆盖旧链接，避免 current 短暂消失。
ln -sfn "${release_dir}" "${next_link}"
mv -Tf "${next_link}" "${current_link}"

mapfile -t old_releases < <(
  find "${releases_dir}" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' \
    | sort -r \
    | tail -n +"$((keep_releases + 1))"
)

for old_release in "${old_releases[@]}"; do
  rm -rf "${releases_dir}/${old_release}"
done
