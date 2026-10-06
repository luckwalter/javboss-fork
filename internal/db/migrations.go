package db

import (
	"context"
	"database/sql"
	"io"
	"io/fs"
	"time"

	"github.com/pressly/goose/v3"
	_ "javboss/internal/db/migrations"
)

const migrationDir = "."

type sqlExecer interface {
	ExecContext(ctx context.Context, query string, args ...any) (sql.Result, error)
}

func init() {
	goose.SetLogger(goose.NopLogger())
	goose.SetBaseFS(emptyMigrationFS{})
	if err := goose.SetDialect("sqlite3"); err != nil {
		panic(err)
	}
}

func runMigrations(ctx context.Context, db *sql.DB) error {
	// [FORK] WithAllowMissing：把「版本号低于已应用最高版本、但当前未执行」的
	// 迁移【补跑】，而不是让 goose 直接 Fatal 拒绝启动。
	//
	// 为什么 fork 必须开这个：goose 用「文件名数字前缀」当迁移唯一标识，
	// fork 自己的迁移用 2099 保留号段（见 internal/db/migrations/209901010001_*），
	// 该号段排在一切上游日期号段之后。于是：
	//   · 上游后续新增的日期号迁移（例 202610060001）版本号恒小于 209901010001，
	//     在 goose 眼里属于 "missing migrations"——默认行为是**报错退出**；
	//   · 开了 allowMissing 后，goose 会把它们放进 migrationsToApply 正常执行
	//     （goose/up.go: `if option.allowMissing { migrationsToApply = missingMigrations }`），
	//     于是「跟官方升级」这条主线不需要任何额外操作。
	//
	// 安全性：被补跑的迁移必须幂等。本项目所有迁移都走 addColumnIfMissing /
	// CREATE INDEX IF NOT EXISTS / columnExists 早退，重复执行无副作用。
	// 2026-10-06 实测：合并上游 main 后，旧库中残留的撞号版本行曾让容器
	// `Restarting (1)`（日志 `found 1 missing migrations before current version 209901010001`），
	// 本选项即该故障的正解。
	return goose.UpContext(ctx, db, migrationDir, goose.WithAllowMissing())
}

func execDB(ctx context.Context, execer sqlExecer, stmt string, args ...any) error {
	_, err := execer.ExecContext(ctx, stmt, args...)
	return err
}

type emptyMigrationFS struct{}

func (emptyMigrationFS) Open(name string) (fs.File, error) {
	if name == "." {
		return emptyMigrationDir{}, nil
	}
	return nil, fs.ErrNotExist
}

type emptyMigrationDir struct{}

func (emptyMigrationDir) Stat() (fs.FileInfo, error) {
	return emptyMigrationDirInfo{}, nil
}

func (emptyMigrationDir) Read([]byte) (int, error) {
	return 0, io.EOF
}

func (emptyMigrationDir) Close() error {
	return nil
}

func (emptyMigrationDir) ReadDir(n int) ([]fs.DirEntry, error) {
	if n > 0 {
		return nil, io.EOF
	}
	return []fs.DirEntry{}, nil
}

type emptyMigrationDirInfo struct{}

func (emptyMigrationDirInfo) Name() string {
	return "."
}

func (emptyMigrationDirInfo) Size() int64 {
	return 0
}

func (emptyMigrationDirInfo) Mode() fs.FileMode {
	return fs.ModeDir | 0o555
}

func (emptyMigrationDirInfo) ModTime() time.Time {
	return time.Time{}
}

func (emptyMigrationDirInfo) IsDir() bool {
	return true
}

func (emptyMigrationDirInfo) Sys() any {
	return nil
}
