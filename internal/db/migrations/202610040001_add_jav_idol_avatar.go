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
func init() {
	goose.AddNamedMigrationContext("202610040001_add_jav_idol_avatar.go", addJavIdolAvatar, irreversibleMigration)
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
