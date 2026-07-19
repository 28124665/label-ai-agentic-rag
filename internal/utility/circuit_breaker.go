//
//  Copyright 2026 The InfiniFlow Authors. All Rights Reserved.
//
//  Licensed under the Apache License, Version 2.0 (the "License");
//  you may not use this file except in compliance with the License.
//  You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
//  Unless required by applicable law or agreed to in writing, software
//  distributed under the License is distributed on an "AS IS" BASIS,
//  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
//  See the License for the specific language governing permissions and
//  limitations under the License.
//

package utility

// Go 层熔断器实现。
//
// 对应需求：P2-NFR-02（优雅降级与熔断机制）
//
// 功能说明：
//   为 Go 服务（如检索 API）提供与 Python 层相同的熔断保护，
//   通过 Redis 共享熔断状态，实现跨语言统一熔断。
//
// 实现方式：
//   1. 状态机与 Python 层完全一致：CLOSED -> OPEN -> HALF_OPEN -> CLOSED
//   2. 通过 Redis Hash 存储状态，与 Python CircuitBreaker 共享
//   3. 通过 Redis Pub/Sub 发布状态变更事件
//   4. Execute 方法封装熔断检查 + 超时控制 + 成功/失败记录
//   5. GetBreaker 全局注册表按服务名管理熔断器实例
//   6. 默认配置与 Python 层 DEFAULT_BREAKER_CONFIGS 对齐

import (
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
	"go.uber.org/zap"

	"ragflow/internal/cache"
	"ragflow/internal/logger"
)

// State represents the circuit breaker state.
type State string

const (
	StateClosed    State = "closed"
	StateOpen      State = "open"
	StateHalfOpen  State = "half_open"
)

const (
	redisKeyPrefix       = "circuit_breaker:"
	redisEventChannel    = "circuit_breaker_events"
	defaultStateTTL      = 7 * 24 * time.Hour
)

// Config holds circuit breaker configuration for a dependency service.
type Config struct {
	FailureThreshold int
	RecoveryTimeout  time.Duration
	HalfOpenMaxCalls int
	TimeoutSeconds   time.Duration
}

// DefaultConfigs provides default parameters aligned with P2-NFR-02.
var DefaultConfigs = map[string]Config{
	"llm":        {FailureThreshold: 5, RecoveryTimeout: 60 * time.Second, HalfOpenMaxCalls: 3, TimeoutSeconds: 30 * time.Second},
	"rerank":     {FailureThreshold: 3, RecoveryTimeout: 30 * time.Second, HalfOpenMaxCalls: 2, TimeoutSeconds: 10 * time.Second},
	"es":         {FailureThreshold: 5, RecoveryTimeout: 60 * time.Second, HalfOpenMaxCalls: 3, TimeoutSeconds: 5 * time.Second},
	"infinity":   {FailureThreshold: 5, RecoveryTimeout: 60 * time.Second, HalfOpenMaxCalls: 3, TimeoutSeconds: 5 * time.Second},
	"embedding":  {FailureThreshold: 5, RecoveryTimeout: 60 * time.Second, HalfOpenMaxCalls: 3, TimeoutSeconds: 10 * time.Second},
	"web_search": {FailureThreshold: 3, RecoveryTimeout: 30 * time.Second, HalfOpenMaxCalls: 2, TimeoutSeconds: 10 * time.Second},
}

// OpenError is returned when the circuit breaker is open.
type OpenError struct {
	Name string
}

func (e *OpenError) Error() string {
	return fmt.Sprintf("circuit breaker '%s' is OPEN", e.Name)
}

// CircuitBreaker is a simple thread-safe circuit breaker with optional Redis shared state.
//
// State machine: CLOSED -> OPEN -> HALF_OPEN -> CLOSED.
//   - CLOSED: requests pass through; consecutive failures are counted.
//   - OPEN: requests are rejected fast until recovery_timeout elapses.
//   - HALF_OPEN: a limited number of probe requests are allowed; enough
//     successes close the breaker, any failure reopens it.
type CircuitBreaker struct {
	name   string
	config Config
	redis  *redis.Client
	mu     sync.RWMutex

	state          State
	failureCount   int64
	successCount   int64
	openedAt       float64
	lastFailureAt  float64
	totalFailures  int64
	totalSuccesses int64
}

// NewCircuitBreaker creates a circuit breaker instance. Use GetBreaker for shared instances.
func NewCircuitBreaker(name string, config Config, redisClient *redis.Client) *CircuitBreaker {
	cb := &CircuitBreaker{
		name:   name,
		config: config,
		redis:  redisClient,
		state:  StateClosed,
	}
	cb.loadState()
	return cb
}

func (cb *CircuitBreaker) redisKey() string {
	return redisKeyPrefix + cb.name
}

func (cb *CircuitBreaker) loadState() {
	if cb.redis == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	data, err := cb.redis.HGetAll(ctx, cb.redisKey()).Result()
	if err != nil || len(data) == 0 {
		return
	}

	if s, ok := data["state"]; ok && s != "" {
		cb.state = State(s)
	}
	cb.failureCount = parseInt64(data["failure_count"])
	cb.successCount = parseInt64(data["success_count"])
	cb.openedAt = parseFloat64(data["opened_at"])
	cb.lastFailureAt = parseFloat64(data["last_failure_at"])
	cb.totalFailures = parseInt64(data["total_failures"])
	cb.totalSuccesses = parseInt64(data["total_successes"])
}

func (cb *CircuitBreaker) saveState() {
	if cb.redis == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	data := map[string]interface{}{
		"state":           string(cb.state),
		"failure_count":   cb.failureCount,
		"success_count":   cb.successCount,
		"opened_at":       cb.openedAt,
		"last_failure_at": cb.lastFailureAt,
		"total_failures":  cb.totalFailures,
		"total_successes": cb.totalSuccesses,
		"updated_at":      float64(time.Now().UnixNano()) / 1e9,
	}
	if err := cb.redis.HSet(ctx, cb.redisKey(), data).Err(); err != nil {
		logger.Warn("Failed to save circuit breaker state", zap.String("name", cb.name), zap.Error(err))
		return
	}
	cb.redis.Expire(ctx, cb.redisKey(), defaultStateTTL)
}

func (cb *CircuitBreaker) publishEvent(event string, detail map[string]interface{}) {
	if cb.redis == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	payload := map[string]interface{}{
		"name":  cb.name,
		"event": event,
		"state": string(cb.state),
		"ts":    float64(time.Now().UnixNano()) / 1e9,
	}
	for k, v := range detail {
		payload[k] = v
	}
	body, _ := json.Marshal(payload)
	if err := cb.redis.Publish(ctx, redisEventChannel, body).Err(); err != nil {
		logger.Warn("Failed to publish circuit breaker event", zap.String("name", cb.name), zap.Error(err))
	}
}

// State returns the current state (loads from Redis first).
func (cb *CircuitBreaker) State() State {
	cb.mu.RLock()
	defer cb.mu.RUnlock()
	cb.loadState()
	return cb.state
}

// AllowRequest returns true when the request is allowed to pass through.
func (cb *CircuitBreaker) AllowRequest() bool {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	cb.loadState()
	now := float64(time.Now().UnixNano()) / 1e9

	switch cb.state {
	case StateOpen:
		if now-cb.openedAt >= cb.config.RecoveryTimeout.Seconds() {
			cb.state = StateHalfOpen
			cb.failureCount = 0
			cb.successCount = 0
			cb.saveState()
			cb.publishEvent("state_changed", map[string]interface{}{"from": "open", "to": "half_open"})
			logger.Warn("Circuit breaker moved to HALF_OPEN", zap.String("name", cb.name))
			return true
		}
		return false
	case StateHalfOpen:
		return cb.successCount < int64(cb.config.HalfOpenMaxCalls)
	default:
		return true
	}
}

// RecordSuccess records a successful call.
func (cb *CircuitBreaker) RecordSuccess() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	cb.loadState()
	cb.totalSuccesses++
	prevState := cb.state

	switch cb.state {
	case StateHalfOpen:
		cb.successCount++
		if cb.successCount >= int64(cb.config.HalfOpenMaxCalls) {
			cb.state = StateClosed
			cb.failureCount = 0
			cb.successCount = 0
			logger.Warn("Circuit breaker moved to CLOSED", zap.String("name", cb.name))
			cb.publishEvent("state_changed", map[string]interface{}{"from": "half_open", "to": "closed"})
		}
	case StateClosed:
		cb.failureCount = 0
	}

	cb.saveState()
	if prevState != cb.state {
		cb.saveState()
	}
}

// RecordFailure records a failed call.
func (cb *CircuitBreaker) RecordFailure() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	cb.loadState()
	cb.totalFailures++
	cb.lastFailureAt = float64(time.Now().UnixNano()) / 1e9
	prevState := cb.state

	switch cb.state {
	case StateHalfOpen:
		cb.state = StateOpen
		cb.failureCount = 1
		cb.successCount = 0
		cb.openedAt = cb.lastFailureAt
		logger.Warn("Circuit breaker moved to OPEN (half-open failure)", zap.String("name", cb.name))
		cb.publishEvent("state_changed", map[string]interface{}{"from": "half_open", "to": "open"})
	case StateClosed:
		cb.failureCount++
		if cb.failureCount >= int64(cb.config.FailureThreshold) {
			cb.state = StateOpen
			cb.openedAt = cb.lastFailureAt
			logger.Warn("Circuit breaker moved to OPEN after consecutive failures",
				zap.String("name", cb.name),
				zap.Int64("failureCount", cb.failureCount))
			cb.publishEvent("state_changed", map[string]interface{}{
				"from":         "closed",
				"to":           "open",
				"failureCount": cb.failureCount,
			})
		}
	}

	cb.saveState()
	if prevState != cb.state {
		cb.saveState()
	}
}

// Execute runs fn when the breaker is closed. If the breaker is open, fallback is used when
// provided, otherwise OpenError is returned. The call is bounded by TimeoutSeconds.
func (cb *CircuitBreaker) Execute(ctx context.Context, fn func(ctx context.Context) (interface{}, error), fallback func() (interface{}, error)) (interface{}, error) {
	if !cb.AllowRequest() {
		if fallback != nil {
			return fallback()
		}
		return nil, &OpenError{Name: cb.name}
	}

	timeout := cb.config.TimeoutSeconds
	if timeout > 0 {
		var cancel context.CancelFunc
		ctx, cancel = context.WithTimeout(ctx, timeout)
		defer cancel()
	}

	result, err := fn(ctx)
	if err != nil {
		cb.RecordFailure()
		return nil, err
	}
	cb.RecordSuccess()
	return result, nil
}

// Reset forces the breaker back to the closed state.
func (cb *CircuitBreaker) Reset() {
	cb.mu.Lock()
	defer cb.mu.Unlock()

	cb.state = StateClosed
	cb.failureCount = 0
	cb.successCount = 0
	cb.openedAt = 0
	cb.saveState()
}

var (
	registry   = make(map[string]*CircuitBreaker)
	registryMu sync.RWMutex
)

// GetBreaker returns a shared circuit breaker instance for the named dependency service.
func GetBreaker(name string) *CircuitBreaker {
	registryMu.RLock()
	b, ok := registry[name]
	registryMu.RUnlock()
	if ok {
		return b
	}

	registryMu.Lock()
	defer registryMu.Unlock()
	if b, ok := registry[name]; ok {
		return b
	}

	cfg, ok := DefaultConfigs[name]
	if !ok {
		cfg = Config{FailureThreshold: 5, RecoveryTimeout: 60 * time.Second, HalfOpenMaxCalls: 3, TimeoutSeconds: 30 * time.Second}
	}

	var redisClient *redis.Client
	if rc := cache.Get(); rc != nil {
		redisClient = rc.GetClient()
	}

	b = NewCircuitBreaker(name, cfg, redisClient)
	registry[name] = b
	return b
}

func parseInt64(s string) int64 {
	v, _ := strconv.ParseInt(s, 10, 64)
	return v
}

func parseFloat64(s string) float64 {
	v, _ := strconv.ParseFloat(s, 64)
	return v
}
