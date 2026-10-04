package server

import (
	"context"
	"database/sql"
	"net/http"
	"os"
	"strings"
	"time"

	"github.com/gin-gonic/gin"

	"javboss/internal/common"
	"javboss/internal/manager"
)

// [FORK] lookupIdolAvatarFile 按 avatar_code 找女优的独立头像本地文件。
// 命中则 /jav/:code/cover 直接吐这张图，不再落到「从作品封面裁一块」的老逻辑，
// 也不会去 provider 下载，更不会牵连该作品自己的封面。
func lookupIdolAvatarFile(code string) (string, bool) {
	if code == "" || common.AppConfig == nil {
		return "", false
	}
	var av sql.NullString
	tx := common.DB.WithContext(context.Background()).
		Raw("SELECT avatar_file FROM jav_idol WHERE avatar_code = ? LIMIT 1", code).
		Scan(&av)
	if tx.Error != nil || !av.Valid {
		return "", false
	}
	p := strings.TrimSpace(av.String)
	if p == "" {
		return "", false
	}
	if fi, err := os.Stat(p); err != nil || fi.IsDir() || fi.Size() <= 0 {
		return "", false
	}
	return p, true
}

// [FORK] hasIdolAvatarFile 判断某个女优是否已挂在本地独立头像文件上。
// 用于避免 enrichJavIdolSummary 把虚拟头像码 GFAV-xxx 回填成「某部作品的封面码」。
func hasIdolAvatarFile(ctx context.Context, idolID int64) bool {
	if idolID <= 0 || common.DB == nil {
		return false
	}
	var av sql.NullString
	tx := common.DB.WithContext(ctx).
		Raw("SELECT avatar_file FROM jav_idol WHERE id = ? LIMIT 1", idolID).
		Scan(&av)
	if tx.Error != nil || !av.Valid {
		return false
	}
	p := strings.TrimSpace(av.String)
	if p == "" {
		return false
	}
	if fi, err := os.Stat(p); err != nil || fi.IsDir() || fi.Size() <= 0 {
		return false
	}
	return true
}

// getJavCover serves a downloaded JAV cover if present; otherwise enqueues and returns 404.
func getJavCover(c *gin.Context) {
	code := c.Param("code")
	cfg := common.AppConfig
	if cfg == nil {
		respondLocalizedError(c, http.StatusInternalServerError, "应用配置尚未加载", "Application configuration is not loaded")
		return
	}

	c.Header("Cache-Control", "no-cache, must-revalidate")

	// [FORK] 女优独立头像优先
	if avatarPath, ok := lookupIdolAvatarFile(code); ok {
		c.File(avatarPath)
		return
	}

	if path, ok := manager.FindCoverPath(cfg.JavCoverDir, code); ok {
		c.File(path)
		return
	}

	if common.CoverManager != nil {
		common.CoverManager.Enqueue(code)
	}
	respondLocalizedError(c, http.StatusNotFound, "JAV 封面不存在", "JAV cover was not found")
}

func updateJavCover(c *gin.Context) {
	code := strings.TrimSpace(c.Param("code"))
	cfg := common.AppConfig
	if cfg == nil {
		respondLocalizedError(c, http.StatusInternalServerError, "应用配置尚未加载", "Application configuration is not loaded")
		return
	}

	var req struct {
		URL string `json:"url"`
	}
	if err := c.ShouldBindJSON(&req); err != nil {
		respondLocalizedError(c, http.StatusBadRequest, "更新 JAV 封面请求无效", "Invalid JAV cover update request")
		return
	}

	ctx, cancel := context.WithTimeout(c.Request.Context(), 45*time.Second)
	defer cancel()
	if err := manager.DownloadCoverFromURL(ctx, cfg.JavCoverDir, code, req.URL); err != nil {
		respondLocalizedError(c, http.StatusBadRequest, "下载 JAV 封面失败，请检查图片地址", "Failed to download the JAV cover; check the image URL")
		return
	}
	c.JSON(http.StatusOK, gin.H{"code": strings.ToLower(code)})
}
