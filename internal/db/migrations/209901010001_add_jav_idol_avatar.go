package migrations

import (
	"context"
	"database/sql"

	"github.com/pressly/goose/v3"
)

// [FORK] 给女优加「独立头像」支持：
//   avatar_code —— 前端拼封面 URL 用的虚拟识别码（走 /jav/:code/cover 路由）
//   avatar_file —— 本地头像文件绝对路径（Gfriends 头像仓库）
//
// 之所以需要 avatar_code：原版女优头像是「从某部作品的封面裁一块」，
// 头像与作品封面共用同一张图。加了 avatar_code 后，
// 女优列表返回的 cover_code 优先取 avatar_code，
// getJavCover 命中 avatar_code 时直接吐 avatar_file，
// 就能把「女优头像」和「作品封面」彻底分开。
//
// 版本号为什么是 2099xxxx：
//   本迁移原本是 202610040001_add_jav_idol_avatar.go，但上游在同期开发中
//   新增了同为 202610040001 的 add_watched_time 迁移。goose 用「版本号数字前缀」
//   作为迁移标识（AddNamedMigrationContext → NumericComponent(name)），
//   两个文件版本号相同时会在全局注册表里互相覆盖，导致其中一个被静默跳过。
//   因此 fork 的迁移统一使用 2099 保留号段，与上游的日期号段彻底隔离，
//   后续跟官方升级不会再撞车。
//   ⚠️ 升级到本版本时，旧库需要把 goose_db_version 里的 202610040001 行清掉
//   （见 docs/maintenance-zh.md「跟官方升级」一节），否则上游的
//   202610040001_add_watched_time 会被误判为「已执行」而跳过。
func init() {
	goose.AddNamedMigrationContext("209901010001_add_jav_idol_avatar.go", addJavIdolAvatar, irreversibleMigration)
}

func addJavIdolAvatar(ctx context.Context, tx *sql.Tx) error {
	if err := addColumnIfMissing(ctx, tx, "jav_idol", "avatar_code", "text"); err != nil {
		return err
	}
	if err := addColumnIfMissing(ctx, tx, "jav_idol", "avatar_file", "text"); err != nil {
		return err
	}
	return execStatements(ctx, tx,
		`CREATE INDEX IF NOT EXISTS idx_jav_idol_avatar_code ON jav_idol(avatar_code)`,
	)
}
