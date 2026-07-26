# Database MCP Server 设计文档

> **项目名称**：鸿海集团 Agentic RAG 智能知识问答系统 —— Database MCP Server
> **文档版本**：V2.1
> **定位**：为 Database Tool 提供按业务域拆分的细粒度数据库访问能力
> **关联文档**：[Database Tool PRD](./dbtoolprd.md)
> **设计原则**：分多个 Tool + 按需分步注入 Schema
> **技术栈**：Java 17 + Spring Boot 3 + MCP Java SDK

---

## 一、概述

### 1.1 定位与职责

Database MCP Server 是 Database Tool 架构中的**数据访问层**，遵循 MCP（Model Context Protocol）标准协议，按业务域为上层 Agent 提供**细粒度、语义明确**的多个 Tool。

**职责边界：**

| 层级 | 组件 | 职责 |
|------|------|------|
| 上层 | Agent / Database Tool | 意图识别、Tool 路由、SQL 生成（NL-to-SQL）、结果格式化 |
| **本层** | **Database MCP Server** | **按业务域暴露 Tool、SQL 执行、Schema 被动响应、安全校验** |
| 底层 | 各业务数据库 | 实际数据存储 |

**核心原则：**

| 原则 | 说明 |
|------|------|
| **分多个 Tool** | 每个数据库按业务域拆分为独立 Tool（`query_{db_id}`），每个 Tool 有唯一 `name` 和聚焦的 `description` |
| **Tool 描述只含意图触发词** | `description` 只描述业务用途和触发场景，**严禁**罗列表名、字段名等物理存储细节 |
| **动态 Tool 生成** | 通过配置文件动态生成 Tool，新增数据库只需添加配置，无需改代码 |
| **Schema 不主动注入** | **禁止**在启动时或连接时将全量 Schema 推送给模型，Schema 只能通过 `list_tables` 和 `describe_table` **被动响应** Agent 的主动调用 |
| **不做 Text-to-SQL** | MCP Server 只接收并执行已生成的 SQL |
| **不做结果自然语言化** | 只返回原始查询结果 |
| **安全拦截在 MCP 层** | 只读校验、表白名单、行数限制在 MCP Server 层完成 |

### 1.2 支持的数据库清单

| 数据库标识 | 系统名称 | 数据库类型 | 业务场景 | 典型查询示例 |
|-----------|---------|-----------|---------|-------------|
| `erp-prod` | ERP 生产数据库 | MySQL（SAP HANA 兼容） | 财务、供应链、采购 | "Q3 各厂区产量是多少？" |
| `mes-prod` | MES 制造执行系统 | MySQL | 生产工单、设备状态、在制品 | "A 产线设备 OEE 是多少？" |
| `qms-prod` | QMS 品质管理系统 | Oracle | 来料检验、制程检验、出货检验 | "本月 iPhone 15 良率是多少？" |
| `clean-db` | 数据清洗库 | PostgreSQL | 清洗后的结构化数据 | "清洗后的供应链数据有多少条？" |
| `plm-prod` | PLM 产品生命周期管理 | PostgreSQL | BOM、工程变更、工艺参数 | "BOM 中 SKU-1001 的物料清单是什么？" |
| `wms-prod` | WMS 仓储管理系统 | Oracle | 库存、出入库、库位管理 | "SKU-1001 当前库存是多少？" |

### 1.3 数据库类型分布

| 数据库类型 | 数量 | 数据库标识 | JDBC 驱动 |
|-----------|------|-----------|----------|
| MySQL | 2 | `erp-prod`, `mes-prod` | `mysql-connector-j`（MySQL 官方 JDBC） |
| PostgreSQL | 2 | `clean-db`, `plm-prod` | `postgresql`（pgjdbc） |
| Oracle | 2 | `qms-prod`, `wms-prod` | `ojdbc11`（Oracle 官方 JDBC） |

### 1.4 技术栈清单

| 分类 | 技术选型 | 版本 | 说明 |
|------|---------|------|------|
| **语言** | Java | 17（LTS） | 使用 Record、sealed class、text block 等新特性 |
| **框架** | Spring Boot | 3.x | 自动配置、依赖注入、健康检查 |
| **MCP 协议** | MCP Java SDK（Spring AI MCP） | latest | MCP Server 标准实现，支持 SSE + Streamable HTTP |
| **连接池** | HikariCP | 5.x（Spring Boot 默认） | 高性能 JDBC 连接池，每库独立池 |
| **JDBC 驱动** | mysql-connector-j / postgresql / ojdbc11 | 各数据库最新稳定版 | 覆盖 MySQL、PostgreSQL、Oracle |
| **配置** | Spring Boot YAML + SnakeYAML | 内置 | 加载 `mcp_db_server.yaml` 配置 |
| **构建** | Maven / Gradle | — | 推荐 Maven，多模块管理 |
| **日志** | SLF4J + Logback | 内置 | 结构化 JSON 日志 |
| **监控** | Micrometer + Prometheus | 可选 | 查询次数、延迟、连接池指标 |


---

## 二、整体架构

### 2.1 架构总览

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Database Tool（上层调用方）                        │
│  通过 MCPToolCallSession 调用 MCP Server                            │
└─────────────────────────────────────────────────────────────────────┘
                    │
                    │  MCP 协议（SSE / Streamable HTTP）
                    │  tool_call: execute_sql / get_schema / list_tables
                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Database MCP Server                               │
│                                                                     │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    MCP 协议层                                  │  │
│  │  • Tool 定义（execute_sql / get_schema / list_tables）         │  │
│  │  • 请求解析与响应序列化                                        │  │
│  │  • 认证与鉴权（API Key / Bearer Token）                       │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                          │                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    安全控制层                                  │  │
│  │  • SQL 只读校验（拦截 DROP/DELETE/UPDATE/INSERT）              │  │
│  │  • 表白名单校验（仅允许配置中定义的表）                        │  │
│  │  • 行数限制（自动追加 LIMIT）                                  │  │
│  │  • 查询超时控制                                                │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                          │                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    数据库适配器层（策略模式）                    │  │
│  │                                                               │  │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐           │  │
│  │  │ MySQLAdapter │  │ PGAdapter   │  │OracleAdapter│           │  │
│  │  │ (HikariCP)  │  │ (HikariCP)  │  │ (HikariCP)  │           │  │
│  │  └─────────────┘  └─────────────┘  └─────────────┘           │  │
│  │         ↑                ↑                ↑                   │  │
│  │         └────────────────┴────────────────┘                   │  │
│  │                    BaseDBAdapter（抽象基类）                    │  │
│  └───────────────────────────────────────────────────────────────┘  │
│                          │                                          │
│  ┌───────────────────────────────────────────────────────────────┐  │
│  │                    连接池管理层                                │  │
│  │  • 每库独立连接池                                              │  │
│  │  • 连接健康检查                                                │  │
│  │  • 连接池大小动态配置                                          │  │
│  └───────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────┐
│  MySQL 集群   │   │ PostgreSQL   │   │  Oracle 集群  │
│  erp-prod    │   │  clean-db    │   │  qms-prod    │
│  mes-prod    │   │  plm-prod    │   │  wms-prod    │
└──────────────┘   └──────────────┘   └──────────────┘
```

### 2.2 部署模式

Database MCP Server 作为**独立进程**部署，通过 MCP 协议与 RAGFlow Agent 通信：

```
RAGFlow Agent（主进程，端口 9380）
    │
    │  MCPToolCallSession
    │  SSE / Streamable HTTP
    ▼
Database MCP Server（独立进程，端口 9383）
    │
    │  数据库连接池
    ▼
各业务数据库
```

**部署方式选择：**

| 方案 | 说明 | 推荐度 |
|------|------|--------|
| **单实例多库** | 一个 MCP Server 进程连接所有 6 个数据库 | ⭐⭐⭐⭐⭐ 推荐 |
| 多实例单库 | 每个数据库一个 MCP Server 进程 | ⭐⭐⭐ 运维复杂，但隔离性更强 |
| 多实例分类 | 按数据库类型分 3 个 MCP Server | ⭐⭐⭐ 折中方案 |

**推荐"单实例多库"方案的理由：**
- 6 个数据库规模可控，单实例完全胜任
- 运维简单，只需管理一个进程
- Database Tool 通过 `db_name` 参数指定目标数据库，路由逻辑清晰
- 未来如需隔离，可平滑拆分为多实例


---

## 三、核心设计

### 3.1 数据库适配器（策略模式）

不同数据库类型的连接和执行逻辑各异，采用**策略模式**封装差异，对外暴露统一接口。

#### 3.1.1 类图

```
                    ┌─────────────────────┐
                    │   BaseDBAdapter     │
                    │   (Abstract Base)   │
                    ├─────────────────────┤
                    │ + connect()         │
                    │ + execute_sql()     │
                    │ + get_schema()      │
                    │ + list_tables()     │
                    │ + validate_sql()    │
                    │ + close()           │
                    └─────────┬───────────┘
                              │
              ┌───────────────┼───────────────┐
              │               │               │
    ┌─────────┴──────┐ ┌─────┴──────┐ ┌──────┴─────────┐
    │  MySQLAdapter   │ │ PGAdapter  │ │ OracleAdapter   │
    │  pymysql        │ │ psycopg2   │ │ oracledb        │
    └────────────────┘ └────────────┘ └────────────────┘
```

#### 3.1.2 抽象基类定义

```java
/**
 * 数据库适配器抽象基类
 * 
 * 所有数据库类型的适配器必须实现以下方法。
 * 设计思路：
 * - 定义统一接口（方法签名）
 * - 提供部分通用实现（如 SQL 只读校验）
 * - 子类只需实现与数据库类型相关的差异部分
 */
public abstract class BaseDBAdapter {
    
    protected final DbConfig dbConfig;
    protected final String dbName;
    protected DataSource dataSource;  // 连接池，由子类初始化
    
    protected BaseDBAdapter(DbConfig dbConfig) {
        this.dbConfig = dbConfig;
        this.dbName = dbConfig.getDbName();
    }
    
    /**
     * 建立数据库连接（初始化连接池）
     */
    public abstract void connect() throws DatabaseException;
    
    /**
     * 执行 SQL 查询
     * 
     * @param sql 要执行的 SQL 语句（仅允许 SELECT）
     * @param params 参数化查询的参数
     * @param maxRows 最大返回行数
     * @param timeout 查询超时时间（秒）
     * @return QueryResult 统一结果对象
     * @throws SecurityException SQL 包含禁止的操作
     * @throws QueryTimeoutException 查询超时
     * @throws DatabaseException 数据库连接或执行错误
     */
    public abstract QueryResult executeSql(String sql, Map<String, Object> params, 
                                           int maxRows, int timeout) throws DatabaseException;
    
    /**
     * 获取表 Schema 信息
     * 
     * @param tableNames 指定表名列表，null 表示获取所有白名单表
     * @return TableSchema 列表
     */
    public abstract List<TableSchema> getSchema(List<String> tableNames) throws DatabaseException;
    
    /**
     * 获取当前数据库可访问的表白名单
     */
    public abstract List<String> listTables() throws DatabaseException;
    
    /**
     * 关闭数据库连接（释放连接池）
     */
    public abstract void close() throws DatabaseException;
    
    /**
     * 校验 SQL 是否为只读操作（通用实现，子类可直接复用）
     * 
     * 拦截策略：
     * 1. 关键词黑名单：DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE/CREATE/EXEC
     * 2. 必须以 SELECT/WITH 开头
     * 3. 禁止多语句执行（禁止分号分隔的多条 SQL）
     */
    protected void validateSqlReadonly(String sql) {
        String sqlUpper = sql.trim().toUpperCase();
        
        // 禁止多语句
        if (sqlUpper.replaceAll(";$", "").contains(";")) {
            throw new SecurityException("多语句执行被禁止（不允许分号分隔的多条 SQL）");
        }
        
        // 必须以 SELECT 或 WITH 开头
        if (!(sqlUpper.startsWith("SELECT") || sqlUpper.startsWith("WITH"))) {
            throw new SecurityException("仅允许 SELECT/WITH 查询，当前 SQL 以 " + 
                sqlUpper.split("\\s+")[0] + " 开头");
        }
        
        // 关键词黑名单
        Set<String> dangerousKeywords = Set.of(
            "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", 
            "TRUNCATE", "CREATE", "EXEC", "EXECUTE", "MERGE"
        );
        
        Set<String> sqlWords = Set.of(sqlUpper.split("\\s+"));
        for (String keyword : dangerousKeywords) {
            if (sqlWords.contains(keyword)) {
                throw new SecurityException("SQL 包含禁止关键词: " + keyword);
            }
        }
    }
}

/**
 * 统一的查询结果数据结构
 */
@Data
@Builder
public class QueryResult {
    private List<String> columns;      // 列名列表
    private List<List<Object>> rows;   // 数据行
    private int rowCount;              // 返回行数
    private long queryTimeMs;          // 查询耗时（毫秒）
    private boolean truncated;         // 是否因行数限制被截断
    private String warning;            // 警告信息（如自动追加 LIMIT）
}

/**
 * 列 Schema 定义
 */
@Data
@Builder
public class ColumnSchema {
    private String name;
    private String dataType;
    private boolean nullable;
    private String comment;
}

/**
 * 表 Schema 定义
 */
@Data
@Builder
public class TableSchema {
    private String name;
    private String comment;
    private List<ColumnSchema> columns;
}
```

#### 3.1.3 MySQL 适配器

```java
/**
 * MySQL 数据库适配器
 * 
 * 适用数据库：erp-prod（SAP HANA MySQL 兼容）、mes-prod
 * 驱动：mysql-connector-j + HikariCP 连接池
 */
@Component
public class MySQLAdapter extends BaseDBAdapter {
    
    private HikariDataSource hikariDataSource;
    
    public MySQLAdapter(DbConfig dbConfig) {
        super(dbConfig);
    }
    
    @Override
    public void connect() {
        HikariConfig config = new HikariConfig();
        config.setJdbcUrl(String.format("jdbc:mysql://%s:%d/%s?useSSL=false&characterEncoding=utf8mb4",
            dbConfig.getHost(), dbConfig.getPort(), dbConfig.getDatabase()));
        config.setUsername(dbConfig.getUser());
        config.setPassword(dbConfig.getPassword());
        config.setMinimumIdle(dbConfig.getPoolMin());
        config.setMaximumPoolSize(dbConfig.getPoolMax());
        config.setConnectionTimeout(30000);
        config.setIdleTimeout(600000);
        config.setMaxLifetime(1800000);
        
        this.hikariDataSource = new HikariDataSource(config);
        this.dataSource = hikariDataSource;
    }
    
    @Override
    public QueryResult executeSql(String sql, Map<String, Object> params, 
                                  int maxRows, int timeout) throws DatabaseException {
        // 1. 只读校验
        validateSqlReadonly(sql);
        
        // 2. 自动追加 LIMIT（防止全表扫描）
        sql = autoLimit(sql, maxRows);
        
        // 3. 执行查询
        long startTime = System.currentTimeMillis();
        try (Connection conn = dataSource.getConnection();
             PreparedStatement pstmt = conn.prepareStatement(sql)) {
            
            // 设置查询超时
            pstmt.setQueryTimeout(timeout);
            
            // 设置参数
            if (params != null) {
                for (Map.Entry<String, Object> entry : params.entrySet()) {
                    pstmt.setObject(entry.getKey(), entry.getValue());
                }
            }
            
            try (ResultSet rs = pstmt.executeQuery()) {
                ResultSetMetaData metaData = rs.getMetaData();
                int columnCount = metaData.getColumnCount();
                
                List<String> columns = new ArrayList<>();
                for (int i = 1; i <= columnCount; i++) {
                    columns.add(metaData.getColumnName(i));
                }
                
                List<List<Object>> rows = new ArrayList<>();
                int rowCount = 0;
                while (rs.next() && rowCount < maxRows) {
                    List<Object> row = new ArrayList<>();
                    for (int i = 1; i <= columnCount; i++) {
                        row.add(rs.getObject(i));
                    }
                    rows.add(row);
                    rowCount++;
                }
                
                long queryTimeMs = System.currentTimeMillis() - startTime;
                
                return QueryResult.builder()
                    .columns(columns)
                    .rows(rows)
                    .rowCount(rowCount)
                    .queryTimeMs(queryTimeMs)
                    .truncated(rowCount >= maxRows)
                    .build();
            }
        } catch (SQLException e) {
            throw new DatabaseException("SQL 执行失败: " + e.getMessage(), e);
        }
    }
    
    @Override
    public List<TableSchema> getSchema(List<String> tableNames) throws DatabaseException {
        List<String> tables = tableNames != null ? tableNames : listTables();
        List<TableSchema> schemas = new ArrayList<>();
        
        String sql = """
            SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_COMMENT
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
            ORDER BY ORDINAL_POSITION
            """;
        
        for (String table : tables) {
            try (Connection conn = dataSource.getConnection();
                 PreparedStatement pstmt = conn.prepareStatement(sql)) {
                
                pstmt.setString(1, dbConfig.getDatabase());
                pstmt.setString(2, table);
                
                try (ResultSet rs = pstmt.executeQuery()) {
                    List<ColumnSchema> columns = new ArrayList<>();
                    while (rs.next()) {
                        columns.add(ColumnSchema.builder()
                            .name(rs.getString("COLUMN_NAME"))
                            .dataType(rs.getString("DATA_TYPE"))
                            .nullable("YES".equals(rs.getString("IS_NULLABLE")))
                            .comment(rs.getString("COLUMN_COMMENT"))
                            .build());
                    }
                    schemas.add(TableSchema.builder()
                        .name(table)
                        .columns(columns)
                        .build());
                }
            } catch (SQLException e) {
                throw new DatabaseException("获取表结构失败: " + e.getMessage(), e);
            }
        }
        return schemas;
    }
    
    @Override
    public List<String> listTables() {
        return dbConfig.getAllowedTables();
    }
    
    @Override
    public void close() {
        if (hikariDataSource != null && !hikariDataSource.isClosed()) {
            hikariDataSource.close();
        }
    }
    
    private String autoLimit(String sql, int maxRows) {
        String sqlUpper = sql.trim().toUpperCase();
        if (!sqlUpper.contains("LIMIT")) {
            sql = sql.replaceAll(";$", "") + " LIMIT " + maxRows;
        }
        return sql;
    }
}
```

#### 3.1.4 PostgreSQL 适配器

```java
/**
 * PostgreSQL 数据库适配器
 * 
 * 适用数据库：clean-db（数据清洗库）、plm-prod（PLM）
 * 驱动：postgresql + HikariCP 连接池
 */
@Component
public class PostgreSQLAdapter extends BaseDBAdapter {
    
    private HikariDataSource hikariDataSource;
    
    public PostgreSQLAdapter(DbConfig dbConfig) {
        super(dbConfig);
    }
    
    @Override
    public void connect() {
        HikariConfig config = new HikariConfig();
        config.setJdbcUrl(String.format("jdbc:postgresql://%s:%d/%s",
            dbConfig.getHost(), dbConfig.getPort(), dbConfig.getDatabase()));
        config.setUsername(dbConfig.getUser());
        config.setPassword(dbConfig.getPassword());
        config.setMinimumIdle(dbConfig.getPoolMin());
        config.setMaximumPoolSize(dbConfig.getPoolMax());
        config.setConnectionTimeout(30000);
        
        this.hikariDataSource = new HikariDataSource(config);
        this.dataSource = hikariDataSource;
    }
    
    @Override
    public QueryResult executeSql(String sql, Map<String, Object> params, 
                                  int maxRows, int timeout) throws DatabaseException {
        validateSqlReadonly(sql);
        sql = autoLimit(sql, maxRows);
        
        long startTime = System.currentTimeMillis();
        try (Connection conn = dataSource.getConnection();
             PreparedStatement pstmt = conn.prepareStatement(sql)) {
            
            // 设置查询超时（PostgreSQL 使用 statement_timeout）
            try (Statement stmt = conn.createStatement()) {
                stmt.execute("SET statement_timeout = " + (timeout * 1000));
            }
            
            if (params != null) {
                for (Map.Entry<String, Object> entry : params.entrySet()) {
                    pstmt.setObject(entry.getKey(), entry.getValue());
                }
            }
            
            try (ResultSet rs = pstmt.executeQuery()) {
                ResultSetMetaData metaData = rs.getMetaData();
                int columnCount = metaData.getColumnCount();
                
                List<String> columns = new ArrayList<>();
                for (int i = 1; i <= columnCount; i++) {
                    columns.add(metaData.getColumnName(i));
                }
                
                List<List<Object>> rows = new ArrayList<>();
                int rowCount = 0;
                while (rs.next() && rowCount < maxRows) {
                    List<Object> row = new ArrayList<>();
                    for (int i = 1; i <= columnCount; i++) {
                        row.add(rs.getObject(i));
                    }
                    rows.add(row);
                    rowCount++;
                }
                
                long queryTimeMs = System.currentTimeMillis() - startTime;
                
                return QueryResult.builder()
                    .columns(columns)
                    .rows(rows)
                    .rowCount(rowCount)
                    .queryTimeMs(queryTimeMs)
                    .truncated(rowCount >= maxRows)
                    .build();
            }
        } catch (SQLException e) {
            throw new DatabaseException("SQL 执行失败: " + e.getMessage(), e);
        }
    }
    
    @Override
    public List<TableSchema> getSchema(List<String> tableNames) throws DatabaseException {
        List<String> tables = tableNames != null ? tableNames : listTables();
        List<TableSchema> schemas = new ArrayList<>();
        
        String sql = """
            SELECT column_name, data_type, is_nullable, 
                   col_description((table_schema||'.'||table_name)::regclass, ordinal_position)
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
            ORDER BY ordinal_position
            """;
        
        for (String table : tables) {
            try (Connection conn = dataSource.getConnection();
                 PreparedStatement pstmt = conn.prepareStatement(sql)) {
                
                pstmt.setString(1, table);
                
                try (ResultSet rs = pstmt.executeQuery()) {
                    List<ColumnSchema> columns = new ArrayList<>();
                    while (rs.next()) {
                        columns.add(ColumnSchema.builder()
                            .name(rs.getString("column_name"))
                            .dataType(rs.getString("data_type"))
                            .nullable("YES".equals(rs.getString("is_nullable")))
                            .comment(rs.getString("col_description"))
                            .build());
                    }
                    schemas.add(TableSchema.builder()
                        .name(table)
                        .columns(columns)
                        .build());
                }
            } catch (SQLException e) {
                throw new DatabaseException("获取表结构失败: " + e.getMessage(), e);
            }
        }
        return schemas;
    }
    
    @Override
    public List<String> listTables() {
        return dbConfig.getAllowedTables();
    }
    
    @Override
    public void close() {
        if (hikariDataSource != null && !hikariDataSource.isClosed()) {
            hikariDataSource.close();
        }
    }
    
    private String autoLimit(String sql, int maxRows) {
        String sqlUpper = sql.trim().toUpperCase();
        if (!sqlUpper.contains("LIMIT")) {
            sql = sql.replaceAll(";$", "") + " LIMIT " + maxRows;
        }
        return sql;
    }
}
```

#### 3.1.5 Oracle 适配器

```java
/**
 * Oracle 数据库适配器
 * 
 * 适用数据库：qms-prod（QMS）、wms-prod（WMS）
 * 驱动：ojdbc11 + HikariCP 连接池
 */
@Component
public class OracleAdapter extends BaseDBAdapter {
    
    private HikariDataSource hikariDataSource;
    
    public OracleAdapter(DbConfig dbConfig) {
        super(dbConfig);
    }
    
    @Override
    public void connect() {
        HikariConfig config = new HikariConfig();
        config.setJdbcUrl(String.format("jdbc:oracle:thin:@%s:%d:%s",
            dbConfig.getHost(), dbConfig.getPort(), dbConfig.getDatabase()));
        config.setUsername(dbConfig.getUser());
        config.setPassword(dbConfig.getPassword());
        config.setMinimumIdle(dbConfig.getPoolMin());
        config.setMaximumPoolSize(dbConfig.getPoolMax());
        config.setConnectionTimeout(30000);
        
        this.hikariDataSource = new HikariDataSource(config);
        this.dataSource = hikariDataSource;
    }
    
    @Override
    public QueryResult executeSql(String sql, Map<String, Object> params, 
                                  int maxRows, int timeout) throws DatabaseException {
        validateSqlReadonly(sql);
        sql = autoLimit(sql, maxRows);
        
        long startTime = System.currentTimeMillis();
        try (Connection conn = dataSource.getConnection();
             PreparedStatement pstmt = conn.prepareStatement(sql)) {
            
            // Oracle 使用setQueryTimeout
            pstmt.setQueryTimeout(timeout);
            
            if (params != null) {
                for (Map.Entry<String, Object> entry : params.entrySet()) {
                    pstmt.setObject(entry.getKey(), entry.getValue());
                }
            }
            
            try (ResultSet rs = pstmt.executeQuery()) {
                ResultSetMetaData metaData = rs.getMetaData();
                int columnCount = metaData.getColumnCount();
                
                List<String> columns = new ArrayList<>();
                for (int i = 1; i <= columnCount; i++) {
                    columns.add(metaData.getColumnName(i));
                }
                
                List<List<Object>> rows = new ArrayList<>();
                int rowCount = 0;
                while (rs.next() && rowCount < maxRows) {
                    List<Object> row = new ArrayList<>();
                    for (int i = 1; i <= columnCount; i++) {
                        row.add(rs.getObject(i));
                    }
                    rows.add(row);
                    rowCount++;
                }
                
                long queryTimeMs = System.currentTimeMillis() - startTime;
                
                return QueryResult.builder()
                    .columns(columns)
                    .rows(rows)
                    .rowCount(rowCount)
                    .queryTimeMs(queryTimeMs)
                    .truncated(rowCount >= maxRows)
                    .build();
            }
        } catch (SQLException e) {
            throw new DatabaseException("SQL 执行失败: " + e.getMessage(), e);
        }
    }
    
    @Override
    public List<TableSchema> getSchema(List<String> tableNames) throws DatabaseException {
        List<String> tables = tableNames != null ? tableNames : listTables();
        List<TableSchema> schemas = new ArrayList<>();
        
        String sql = """
            SELECT column_name, data_type, nullable, comments
            FROM user_tab_columns utc
            LEFT JOIN user_col_comments ucc 
                ON utc.table_name = ucc.table_name AND utc.column_name = ucc.column_name
            WHERE utc.table_name = ?
            ORDER BY column_id
            """;
        
        for (String table : tables) {
            try (Connection conn = dataSource.getConnection();
                 PreparedStatement pstmt = conn.prepareStatement(sql)) {
                
                pstmt.setString(1, table.toUpperCase());
                
                try (ResultSet rs = pstmt.executeQuery()) {
                    List<ColumnSchema> columns = new ArrayList<>();
                    while (rs.next()) {
                        columns.add(ColumnSchema.builder()
                            .name(rs.getString("column_name"))
                            .dataType(rs.getString("data_type"))
                            .nullable("Y".equals(rs.getString("nullable")))
                            .comment(rs.getString("comments"))
                            .build());
                    }
                    schemas.add(TableSchema.builder()
                        .name(table)
                        .columns(columns)
                        .build());
                }
            } catch (SQLException e) {
                throw new DatabaseException("获取表结构失败: " + e.getMessage(), e);
            }
        }
        return schemas;
    }
    
    @Override
    public List<String> listTables() {
        return dbConfig.getAllowedTables();
    }
    
    @Override
    public void close() {
        if (hikariDataSource != null && !hikariDataSource.isClosed()) {
            hikariDataSource.close();
        }
    }
    
    private String autoLimit(String sql, int maxRows) {
        // Oracle 12c+ 支持 FETCH FIRST，低版本使用 ROWNUM
        String sqlUpper = sql.trim().toUpperCase();
        if (!sqlUpper.contains("FETCH FIRST") && !sqlUpper.contains("ROWNUM")) {
            sql = sql.replaceAll(";$", "") + " FETCH FIRST " + maxRows + " ROWS ONLY";
        }
        return sql;
    }
}
```

#### 3.1.6 适配器工厂

```python
from enum import Enum

class DBType(str, Enum):
    MYSQL = "mysql"
    POSTGRESQL = "postgresql"
    ORACLE = "oracle"

class DBAdapterFactory:
    """数据库适配器工厂（工厂方法模式）
    
    根据数据库类型创建对应的适配器实例。
    新增数据库类型只需：
    1. 实现新的 Adapter 类
    2. 在工厂中注册
    """
    
    _ADAPTER_MAP = {
        DBType.MYSQL: MySQLAdapter,
        DBType.POSTGRESQL: PostgreSQLAdapter,
        DBType.ORACLE: OracleAdapter,
    }
    
    @classmethod
    def create(cls, db_config: dict) -> BaseDBAdapter:
        db_type = DBType(db_config["db_type"])
        adapter_class = cls._ADAPTER_MAP.get(db_type)
        if not adapter_class:
            raise ValueError(f"不支持的数据库类型: {db_type}")
        return adapter_class(db_config)
```

### 3.2 MCP Tool 定义

Database MCP Server 对外暴露 3 个 Tool：

#### 3.2.1 Tool 列表

| Tool 名称 | 功能 | 输入参数 | 输出 |
|-----------|------|---------|------|
| `execute_sql` | 执行 SQL 查询 | `db_name`, `sql`, `params`, `max_rows`, `timeout` | QueryResult JSON |
| `get_schema` | 获取表结构信息 | `db_name`, `table_names`（可选） | TableSchema JSON |
| `list_tables` | 列出可访问的表 | `db_name` | 表名列表 JSON |
| `health_check` | 检查数据库连接状态 | `db_name`（可选，不传则检查全部） | 各数据库连接状态 |

#### 3.2.2 Tool Schema 定义（使用 MCP Java SDK）

```java
import io.modelcontextprotocol.server.McpServerFeatures;
import io.modelcontextprotocol.spec.McpSchema;
import org.springframework.stereotype.Component;

/**
 * MCP Tool 定义（使用 MCP Java SDK）
 */
@Component
public class McpToolDefinitions {
    
    /**
     * execute_sql Tool 定义
     */
    public McpServerFeatures.ToolRegistration executeSqlTool() {
        return McpServerFeatures.ToolRegistration.builder()
            .name("execute_sql")
            .description("""
                执行 SQL 查询并返回结果。
                
                支持的数据库：
                - erp-prod: ERP 生产数据库（MySQL，SAP HANA 兼容）
                - mes-prod: MES 制造执行系统（MySQL）
                - qms-prod: QMS 品质管理系统（Oracle）
                - clean-db: 数据清洗库（PostgreSQL）
                - plm-prod: PLM 产品生命周期管理（PostgreSQL）
                - wms-prod: WMS 仓储管理系统（Oracle）
                
                注意事项：
                - 仅允许 SELECT 查询，禁止任何写操作
                - 查询结果最多返回 max_rows 行（默认 100）
                - 查询超时时间默认 30 秒
                - 返回结果为 JSON 格式，包含列名、数据行、行数等信息
                """)
            .inputSchema(McpSchema.JsonSchema.builder()
                .type("object")
                .properties(Map.of(
                    "db_name", Map.of(
                        "type", "string",
                        "description", "目标数据库标识（erp-prod/mes-prod/qms-prod/clean-db/plm-prod/wms-prod）",
                        "enum", List.of("erp-prod", "mes-prod", "qms-prod", "clean-db", "plm-prod", "wms-prod")
                    ),
                    "sql", Map.of(
                        "type", "string",
                        "description", "要执行的 SQL 查询语句（仅允许 SELECT/WITH）"
                    ),
                    "params", Map.of(
                        "type", "object",
                        "description", "SQL 参数化查询的参数（可选）"
                    ),
                    "max_rows", Map.of(
                        "type", "integer",
                        "description", "最大返回行数（默认 100，最大 1000）",
                        "default", 100,
                        "minimum", 1,
                        "maximum", 1000
                    ),
                    "timeout", Map.of(
                        "type", "integer",
                        "description", "查询超时时间（秒，默认 30，最大 120）",
                        "default", 30,
                        "minimum", 1,
                        "maximum", 120
                    )
                ))
                .required(List.of("db_name", "sql"))
                .build())
            .build();
    }
    
    /**
     * get_schema Tool 定义
     */
    public McpServerFeatures.ToolRegistration getSchemaTool() {
        return McpServerFeatures.ToolRegistration.builder()
            .name("get_schema")
            .description("""
                获取指定数据库的表结构信息（列名、类型、注释等）。
                
                用于 Database Tool 在生成 SQL 前获取 Schema 上下文。
                如果不指定 table_names，则返回所有白名单表的结构。
                """)
            .inputSchema(McpSchema.JsonSchema.builder()
                .type("object")
                .properties(Map.of(
                    "db_name", Map.of(
                        "type", "string",
                        "description", "目标数据库标识"
                    ),
                    "table_names", Map.of(
                        "type", "array",
                        "items", Map.of("type", "string"),
                        "description", "指定表名列表（可选，不传则返回所有白名单表）"
                    )
                ))
                .required(List.of("db_name"))
                .build())
            .build();
    }
    
    /**
     * list_tables Tool 定义
     */
    public McpServerFeatures.ToolRegistration listTablesTool() {
        return McpServerFeatures.ToolRegistration.builder()
            .name("list_tables")
            .description("列出指定数据库中可访问的表白名单。")
            .inputSchema(McpSchema.JsonSchema.builder()
                .type("object")
                .properties(Map.of(
                    "db_name", Map.of(
                        "type", "string",
                        "description", "目标数据库标识"
                    )
                ))
                .required(List.of("db_name"))
                .build())
            .build();
    }
    
    /**
     * health_check Tool 定义
     */
    public McpServerFeatures.ToolRegistration healthCheckTool() {
        return McpServerFeatures.ToolRegistration.builder()
            .name("health_check")
            .description("检查数据库连接状态。不指定 db_name 则检查所有数据库。")
            .inputSchema(McpSchema.JsonSchema.builder()
                .type("object")
                .properties(Map.of(
                    "db_name", Map.of(
                        "type", "string",
                        "description", "目标数据库标识（可选，不传则检查全部）"
                    )
                ))
                .build())
            .build();
    }
}
```

#### 3.2.3 Tool 调用处理

```java
import io.modelcontextprotocol.server.McpServer;
import io.modelcontextprotocol.server.McpServerFeatures;
import io.modelcontextprotocol.server.McpSyncServer;
import io.modelcontextprotocol.server.transport.WebMvcSseServerTransportProvider;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.servlet.config.annotation.CorsRegistry;
import org.springframework.web.servlet.config.annotation.WebMvcConfigurer;

/**
 * MCP Server 配置
 */
@Configuration
public class McpServerConfig {
    
    @Autowired
    private McpToolDefinitions toolDefinitions;
    
    @Autowired
    private DatabaseToolHandler toolHandler;
    
    /**
     * 配置 MCP Server
     */
    @Bean
    public McpSyncServer mcpServer() {
        return McpServer.sync()
            .serverInfo("database-mcp-server", "2.0.0")
            .transportProvider(transportProvider())
            .tools(
                toolDefinitions.executeSqlTool(),
                toolDefinitions.getSchemaTool(),
                toolDefinitions.listTablesTool(),
                toolDefinitions.healthCheckTool()
            )
            .build();
    }
    
    /**
     * SSE 传输层配置
     */
    @Bean
    public WebMvcSseServerTransportProvider transportProvider() {
        return new WebMvcSseServerTransportProvider();
    }
    
    /**
     * CORS 配置
     */
    @Bean
    public WebMvcConfigurer corsConfigurer() {
        return new WebMvcConfigurer() {
            @Override
            public void addCorsMappings(CorsRegistry registry) {
                registry.addMapping("/mcp/**")
                    .allowedOrigins("*")
                    .allowedMethods("GET", "POST")
                    .allowedHeaders("*");
            }
        };
    }
}

/**
 * Database Tool 调用处理器
 */
@Component
public class DatabaseToolHandler {
    
    @Autowired
    private DatabaseManager databaseManager;
    
    /**
     * 处理 execute_sql 调用
     */
    public McpSchema.CallToolResult handleExecuteSql(Map<String, Object> arguments) {
        String dbName = (String) arguments.get("db_name");
        String sql = (String) arguments.get("sql");
        Map<String, Object> params = (Map<String, Object>) arguments.getOrDefault("params", Map.of());
        int maxRows = (int) arguments.getOrDefault("max_rows", 100);
        int timeout = (int) arguments.getOrDefault("timeout", 30);
        
        maxRows = Math.min(maxRows, 1000);
        timeout = Math.min(timeout, 120);
        
        try {
            BaseDBAdapter adapter = databaseManager.getAdapter(dbName);
            long startTime = System.currentTimeMillis();
            
            QueryResult result = adapter.executeSql(sql, params, maxRows, timeout);
            result.setQueryTimeMs(System.currentTimeMillis() - startTime);
            
            // 序列化为 JSON
            Map<String, Object> response = new HashMap<>();
            response.put("status", "success");
            response.put("db_name", dbName);
            response.put("columns", result.getColumns());
            response.put("rows", result.getRows());
            response.put("row_count", result.getRowCount());
            response.put("query_time_ms", result.getQueryTimeMs());
            response.put("truncated", result.isTruncated());
            
            if (result.getWarning() != null) {
                response.put("warning", result.getWarning());
            }
            
            return McpSchema.CallToolResult.builder()
                .content(List.of(new McpSchema.TextContent(objectMapper.writeValueAsString(response))))
                .build();
                
        } catch (SecurityException e) {
            return errorResult("security", e.getMessage());
        } catch (QueryTimeoutException e) {
            return errorResult("timeout", e.getMessage());
        } catch (DatabaseException e) {
            return errorResult("database", e.getMessage());
        } catch (Exception e) {
            return errorResult("unknown", e.getMessage());
        }
    }
    
    /**
     * 处理 get_schema 调用
     */
    public McpSchema.CallToolResult handleGetSchema(Map<String, Object> arguments) {
        String dbName = (String) arguments.get("db_name");
        List<String> tableNames = (List<String>) arguments.get("table_names");
        
        try {
            BaseDBAdapter adapter = databaseManager.getAdapter(dbName);
            List<TableSchema> schemas = adapter.getSchema(tableNames);
            
            Map<String, Object> response = Map.of(
                "status", "success",
                "db_name", dbName,
                "schemas", schemas
            );
            
            return McpSchema.CallToolResult.builder()
                .content(List.of(new McpSchema.TextContent(objectMapper.writeValueAsString(response))))
                .build();
                
        } catch (Exception e) {
            return errorResult("database", e.getMessage());
        }
    }
    
    /**
     * 处理 list_tables 调用
     */
    public McpSchema.CallToolResult handleListTables(Map<String, Object> arguments) {
        String dbName = (String) arguments.get("db_name");
        
        try {
            BaseDBAdapter adapter = databaseManager.getAdapter(dbName);
            List<String> tables = adapter.listTables();
            
            Map<String, Object> response = Map.of(
                "status", "success",
                "db_name", dbName,
                "tables", tables
            );
            
            return McpSchema.CallToolResult.builder()
                .content(List.of(new McpSchema.TextContent(objectMapper.writeValueAsString(response))))
                .build();
                
        } catch (Exception e) {
            return errorResult("database", e.getMessage());
        }
    }
    
    /**
     * 处理 health_check 调用
     */
    public McpSchema.CallToolResult handleHealthCheck(Map<String, Object> arguments) {
        String dbName = (String) arguments.get("db_name");
        
        try {
            Map<String, Object> healthStatus = databaseManager.healthCheck(dbName);
            
            return McpSchema.CallToolResult.builder()
                .content(List.of(new McpSchema.TextContent(objectMapper.writeValueAsString(healthStatus))))
                .build();
                
        } catch (Exception e) {
            return errorResult("health_check", e.getMessage());
        }
    }
    
    private McpSchema.CallToolResult errorResult(String errorType, String message) {
        Map<String, Object> error = Map.of(
            "status", "error",
            "error_type", errorType,
            "message", message
        );
        return McpSchema.CallToolResult.builder()
            .content(List.of(new McpSchema.TextContent(objectMapper.writeValueAsString(error))))
            .build();
    }
}
```
    elif name == "list_tables":
        return await handle_list_tables(arguments)
    elif name == "health_check":
        return await handle_health_check(arguments)
    else:
        raise ValueError(f"Tool not found: {name}")

async def handle_execute_sql(arguments: dict) -> list[types.TextContent]:
    db_name = arguments["db_name"]
    sql = arguments["sql"]
    params = arguments.get("params", {})
    max_rows = min(arguments.get("max_rows", 100), 1000)
    timeout = min(arguments.get("timeout", 30), 120)
    
    # 获取适配器
    adapter = db_manager.get_adapter(db_name)
    
    # 执行查询
    start_time = time.time()
    try:
        result = await adapter.execute_sql(sql, params, max_rows, timeout)
        result.query_time_ms = round((time.time() - start_time) * 1000, 2)
        
        # 序列化为 JSON
        response = {
            "status": "success",
            "db_name": db_name,
            "columns": result.columns,
            "rows": [dict(zip(result.columns, row)) for row in result.rows],
            "row_count": result.row_count,
            "query_time_ms": result.query_time_ms,
            "truncated": result.truncated,
        }
        if result.warning:
            response["warning"] = result.warning
            
    except SecurityError as e:
        response = {"status": "error", "error_type": "security", "message": str(e)}
    except QueryTimeoutError as e:
        response = {"status": "error", "error_type": "timeout", "message": str(e)}
    except DatabaseError as e:
        response = {"status": "error", "error_type": "database", "message": str(e)}
    
    return [types.TextContent(type="text", text=json.dumps(response, ensure_ascii=False, default=str))]
```

### 3.3 数据库连接管理器

```python
class DatabaseManager:
    """数据库连接管理器（门面模式）
    
    统一管理所有数据库适配器的生命周期：
    - 启动时初始化所有连接池
    - 提供按 db_name 获取适配器的方法
    - 关闭时释放所有连接
    - 提供健康检查能力
    """
    
    def __init__(self, config_path: str):
        self.config = self._load_config(config_path)
        self._adapters: dict[str, BaseDBAdapter] = {}
    
    async def initialize(self) -> None:
        """初始化所有数据库连接池"""
        for db_name, db_config in self.config["databases"].items():
            adapter = DBAdapterFactory.create(db_config)
            await adapter.connect()
            self._adapters[db_name] = adapter
            logging.info(f"[DatabaseManager] Connected to {db_name} ({db_config['db_type']})")
    
    def get_adapter(self, db_name: str) -> BaseDBAdapter:
        """获取指定数据库的适配器"""
        adapter = self._adapters.get(db_name)
        if not adapter:
            raise ValueError(f"未知的数据库标识: {db_name}，可用: {list(self._adapters.keys())}")
        return adapter
    
    async def health_check(self, db_name: str | None = None) -> dict:
        """检查数据库连接状态"""
        targets = {db_name: self._adapters[db_name]} if db_name else self._adapters
        results = {}
        for name, adapter in targets.items():
            try:
                # 执行简单查询验证连接
                await adapter.execute_sql("SELECT 1", max_rows=1, timeout=5)
                results[name] = {"status": "healthy", "db_type": adapter.db_config["db_type"]}
            except Exception as e:
                results[name] = {"status": "unhealthy", "error": str(e)}
        return results
    
    async def shutdown(self) -> None:
        """关闭所有数据库连接"""
        for name, adapter in self._adapters.items():
            try:
                await adapter.close()
                logging.info(f"[DatabaseManager] Disconnected from {name}")
            except Exception as e:
                logging.error(f"[DatabaseManager] Error closing {name}: {e}")
    
    def _load_config(self, config_path: str) -> dict:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
```


---

## 四、配置文件设计

### 4.1 完整配置文件

```yaml
# conf/mcp_db_server.yaml
# Database MCP Server 配置文件

# ============================================================
# MCP Server 基础配置
# ============================================================
server:
  host: "0.0.0.0"
  port: 9383
  transport:
    sse_enabled: true
    streamable_http_enabled: true
    json_response: true
  
  # 认证配置
  auth:
    # 与 RAGFlow Agent 通信的 API Key（在 RAGFlow 中注册此 MCP Server 时使用）
    api_key: "${MCP_DB_SERVER_API_KEY}"
    # 是否启用认证
    auth_enabled: true
  
  # 工具级权限控制（MCP-REQ-04）
  permissions:
    # 按角色/用户组配置可访问的 Tool 列表
    roles:
      # 高管：可访问所有数据库
      executive:
        allowed_tools:
          - "query_*"
          - "list_tables_*"
          - "describe_table_*"
      
      # 中层管理者：只能访问特定业务域
      middle_manager:
        allowed_tools:
          - "query_mes_prod"
          - "list_tables_mes_prod"
          - "describe_table_mes_prod"
          - "query_erp_prod"
          - "list_tables_erp_prod"
          - "describe_table_erp_prod"
      
      # 普通员工：只能查询，不能探查 Schema
      employee:
        allowed_tools:
          - "query_mes_prod"
          - "query_erp_prod"
    
    # 用户到角色的映射（可从 RAGFlow 用户系统同步）
    user_role_mapping:
      "user_executive_001": "executive"
      "user_manager_001": "middle_manager"
      "user_employee_001": "employee"

# ============================================================
# 数据库连接配置
# ============================================================
databases:
  # ──────────────────────────────────────────────
  # ERP 生产数据库（SAP HANA，MySQL 兼容协议）
  # ──────────────────────────────────────────────
  erp-prod:
    db_type: "mysql"
    host: "${ERP_DB_HOST}"
    port: 3306
    database: "erp_prod"
    user: "${ERP_DB_USER}"
    password: "${ERP_DB_PASSWORD}"
    charset: "utf8mb4"
    # 连接池配置
    pool_min: 2
    pool_max: 10
    # 白名单：仅允许 Database Tool 访问这些表
    allowed_tables:
      - production_order        # 生产工单
      - output_record           # 产量记录
      - wip_tracking            # 在制品追踪
      - inventory_current       # 当前库存
      - purchase_order          # 采购订单
      - supplier_score          # 供应商评分
    # 安全配置
    security:
      max_rows_default: 100
      max_rows_limit: 1000
      query_timeout_default: 30
      query_timeout_max: 120
    # 业务描述（用于 Tool description 动态生成）
    description: "ERP 生产数据库（SAP HANA），承载财务、供应链、采购等关键数据"

  # ──────────────────────────────────────────────
  # MES 制造执行系统
  # ──────────────────────────────────────────────
  mes-prod:
    db_type: "mysql"
    host: "${MES_DB_HOST}"
    port: 3306
    database: "mes_prod"
    user: "${MES_DB_USER}"
    password: "${MES_DB_PASSWORD}"
    charset: "utf8mb4"
    pool_min: 2
    pool_max: 10
    allowed_tables:
      - equipment_status        # 设备状态
      - oee_record              # OEE 记录
      - maintenance_log         # 维护日志
      - production_line         # 产线信息
      - work_order              # 工单
    security:
      max_rows_default: 100
      max_rows_limit: 1000
      query_timeout_default: 30
      query_timeout_max: 120
    description: "MES 制造执行系统，管理生产工单、设备状态、在制品追踪等实时生产数据"

  # ──────────────────────────────────────────────
  # QMS 品质管理系统（Oracle）
  # ──────────────────────────────────────────────
  qms-prod:
    db_type: "oracle"
    host: "${QMS_DB_HOST}"
    port: 1521
    database: "QMSDB"           # Oracle SID / Service Name
    user: "${QMS_DB_USER}"
    password: "${QMS_DB_PASSWORD}"
    pool_min: 2
    pool_max: 10
    allowed_tables:
      - IQC_INSPECTION          # 来料检验
      - IPQC_CHECK              # 制程检验
      - FQC_RESULT              # 出货检验
      - DEFECT_RECORD           # 不良记录
      - QUALITY_STANDARD        # 品质标准
    security:
      max_rows_default: 100
      max_rows_limit: 1000
      query_timeout_default: 30
      query_timeout_max: 120
    description: "QMS 品质管理系统，存储来料检验、制程检验、出货检验等品质数据"

  # ──────────────────────────────────────────────
  # 数据清洗库（PostgreSQL）
  # ──────────────────────────────────────────────
  clean-db:
    db_type: "postgresql"
    host: "${CLEAN_DB_HOST}"
    port: 5432
    database: "clean_data"
    user: "${CLEAN_DB_USER}"
    password: "${CLEAN_DB_PASSWORD}"
    pool_min: 2
    pool_max: 10
    allowed_tables:
      - cleaned_supply_chain    # 清洗后的供应链数据
      - cleaned_production      # 清洗后的生产数据
      - cleaned_quality         # 清洗后的品质数据
      - data_lineage            # 数据血缘
    security:
      max_rows_default: 100
      max_rows_limit: 1000
      query_timeout_default: 30
      query_timeout_max: 120
    description: "数据清洗库，存储经过清洗和标准化的结构化数据"

  # ──────────────────────────────────────────────
  # PLM 产品生命周期管理（PostgreSQL）
  # ──────────────────────────────────────────────
  plm-prod:
    db_type: "postgresql"
    host: "${PLM_DB_HOST}"
    port: 5432
    database: "plm_prod"
    user: "${PLM_DB_USER}"
    password: "${PLM_DB_PASSWORD}"
    pool_min: 2
    pool_max: 10
    allowed_tables:
      - bom_structure           # BOM 结构
      - engineering_change      # 工程变更
      - process_parameter       # 工艺参数
      - material_master         # 物料主数据
    security:
      max_rows_default: 100
      max_rows_limit: 1000
      query_timeout_default: 30
      query_timeout_max: 120
    description: "PLM 产品生命周期管理系统，管理 BOM、工程变更、工艺参数等产品数据"

  # ──────────────────────────────────────────────
  # WMS 仓储管理系统（Oracle）
  # ──────────────────────────────────────────────
  wms-prod:
    db_type: "oracle"
    host: "${WMS_DB_HOST}"
    port: 1521
    database: "WMSDB"
    user: "${WMS_DB_USER}"
    password: "${WMS_DB_PASSWORD}"
    pool_min: 2
    pool_max: 10
    allowed_tables:
      - INVENTORY_CURRENT       # 当前库存
      - INBOUND_ORDER           # 入库单
      - OUTBOUND_ORDER          # 出库单
      - WAREHOUSE_LOCATION      # 库位信息
      - MATERIAL_MOVEMENT       # 物料移动记录
    security:
      max_rows_default: 100
      max_rows_limit: 1000
      query_timeout_default: 30
      query_timeout_max: 120
    description: "WMS 仓储管理系统，管理库存、出入库、库位等仓储数据"

# ============================================================
# 日志配置
# ============================================================
logging:
  level: "INFO"
  format: "json"                # json | text
  output: "stdout"              # stdout | file
  file_path: "/var/log/mcp-db-server/mcp-db-server.log"
  # 审计日志：记录每次 SQL 执行
  audit:
    enabled: true
    file_path: "/var/log/mcp-db-server/audit.log"
```

### 4.2 环境变量文件

```bash
# .env.mcp_db_server

# ── MCP Server ──
MCP_DB_SERVER_API_KEY=ragflow-xxxxxxxxxxxx

# ── ERP（MySQL） ──
ERP_DB_HOST=erp-mysql.internal.foxconn.com
ERP_DB_USER=readonly_erp
ERP_DB_PASSWORD=xxxxxxxxxxxx

# ── MES（MySQL） ──
MES_DB_HOST=mes-mysql.internal.foxconn.com
MES_DB_USER=readonly_mes
MES_DB_PASSWORD=xxxxxxxxxxxx

# ── QMS（Oracle） ──
QMS_DB_HOST=qms-oracle.internal.foxconn.com
QMS_DB_USER=readonly_qms
QMS_DB_PASSWORD=xxxxxxxxxxxx

# ── Clean DB（PostgreSQL） ──
CLEAN_DB_HOST=clean-pg.internal.foxconn.com
CLEAN_DB_USER=readonly_clean
CLEAN_DB_PASSWORD=xxxxxxxxxxxx

# ── PLM（PostgreSQL） ──
PLM_DB_HOST=plm-pg.internal.foxconn.com
PLM_DB_USER=readonly_plm
PLM_DB_PASSWORD=xxxxxxxxxxxx

# ── WMS（Oracle） ──
WMS_DB_HOST=wms-oracle.internal.foxconn.com
WMS_DB_USER=readonly_wms
WMS_DB_PASSWORD=xxxxxxxxxxxx
```


---

## 五、安全设计

### 5.1 多层安全防线

```
┌─────────────────────────────────────────────────────────────────┐
│  第一层：数据库账户（只读）                                       │
│  所有连接使用 READ ONLY 权限账户，从数据库层面禁止写操作           │
├─────────────────────────────────────────────────────────────────┤
│  第二层：MCP Server SQL 校验                                     │
│  • 关键词黑名单（DROP/DELETE/UPDATE/INSERT/ALTER/TRUNCATE）       │
│  • 必须以 SELECT/WITH 开头                                      │
│  • 禁止多语句执行                                                │
├─────────────────────────────────────────────────────────────────┤
│  第三层：表白名单                                                │
│  仅允许查询配置中定义的 allowed_tables，其他表不可见               │
├─────────────────────────────────────────────────────────────────┤
│  第四层：行数限制                                                 │
│  自动追加 LIMIT（MySQL/PG）/ FETCH FIRST（Oracle），防止全表扫描   │
├─────────────────────────────────────────────────────────────────┤
│  第五层：查询超时                                                 │
│  默认 30s，最大 120s，超时自动终止                               │
├─────────────────────────────────────────────────────────────────┤
│  第六层：审计日志                                                 │
│  每次查询记录：调用方 → db_name → SQL → 结果行数 → 耗时 → 状态    │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 各数据库只读账户创建示例

```sql
-- MySQL（erp-prod / mes-prod）
CREATE USER 'readonly_erp'@'%' IDENTIFIED BY 'strong_password';
GRANT SELECT ON erp_prod.* TO 'readonly_erp'@'%';
FLUSH PRIVILEGES;

-- PostgreSQL（clean-db / plm-prod）
CREATE ROLE readonly_clean WITH LOGIN PASSWORD 'strong_password';
GRANT CONNECT ON DATABASE clean_data TO readonly_clean;
GRANT USAGE ON SCHEMA public TO readonly_clean;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO readonly_clean;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO readonly_clean;

-- Oracle（qms-prod / wms-prod）
CREATE USER readonly_qms IDENTIFIED BY "strong_password";
GRANT CREATE SESSION TO readonly_qms;
GRANT SELECT ON qms_schema.IQC_INSPECTION TO readonly_qms;
GRANT SELECT ON qms_schema.IPQC_CHECK TO readonly_qms;
-- ... 逐表授权
```

### 5.3 审计日志格式

```json
{
    "timestamp": "2026-07-25T14:30:00.123+08:00",
    "level": "INFO",
    "event": "sql_executed",
    "caller": {
        "mcp_session_id": "abc123",
        "ragflow_tenant_id": "tenant_xxx"
    },
    "database": {
        "db_name": "erp-prod",
        "db_type": "mysql"
    },
    "query": {
        "sql": "SELECT factory_code, SUM(quantity) FROM output_record WHERE record_date BETWEEN '2026-07-01' AND '2026-07-31' GROUP BY factory_code LIMIT 100",
        "params": {},
        "max_rows": 100,
        "timeout": 30
    },
    "result": {
        "status": "success",
        "row_count": 5,
        "query_time_ms": 123.45,
        "truncated": false
    }
}
```


---

## 六、与 RAGFlow 集成

### 6.1 在 RAGFlow 中注册 MCP Server

在 RAGFlow 的 `mcp_server` 表中注册 Database MCP Server：

```sql
INSERT INTO mcp_server (id, name, tenant_id, url, server_type, description, variables, headers)
VALUES (
    'mcp_db_server_001',
    'Database MCP Server',
    'tenant_id_here',
    'http://localhost:9383/mcp',
    'streamable-http',
    '统一数据库查询 MCP Server，支持 MySQL/PostgreSQL/Oracle，连接 ERP/MES/QMS/PLM/WMS/清洗库',
    '{}',
    '{"Authorization": "Bearer ragflow-xxxxxxxxxxxx"}'
);
```

### 6.2 Database Tool 调用流程

```
Database Tool
    │
    │  1. 从 RAGFlow DB 获取 MCP Server 配置
    │     MCPServerService.get_by_id(mcp_server_id)
    │
    │  2. 创建 MCP 会话
    │     session = MCPToolCallSession(mcp_server)
    │
    │  3. 调用 execute_sql Tool
    │     result = session.tool_call(
    │         name="execute_sql",
    │         arguments={
    │             "db_name": "erp-prod",
    │             "sql": "SELECT ...",
    │             "max_rows": 100
    │         }
    │     )
    │
    │  4. 解析 JSON 结果
    │     response = json.loads(result)
    │
    ▼
Database MCP Server（端口 9383）
    │
    │  5. 解析参数
    │  6. 安全校验（只读、白名单、LIMIT）
    │  7. 获取适配器（DBAdapterFactory.create）
    │  8. 执行 SQL
    │  9. 返回 JSON 结果
    │
    ▼
对应数据库（erp-prod / mes-prod / ...）
```

### 6.3 与 Database Tool PRD 的关系

| Database Tool PRD 中的需求 | 本设计文档的覆盖 |
|---------------------------|----------------|
| FR-DB-01 多数据库类型支持 | ✅ 通过适配器模式支持 MySQL/PG/Oracle |
| FR-DB-02 Schema 静态配置 | ✅ 配置文件中 allowed_tables 白名单 |
| FR-DB-04 SQL 安全执行 | ✅ 多层安全防线（只读账户 + SQL 校验 + 白名单 + LIMIT） |
| FR-DB-12 MCP 数据库连接 | ✅ 完整的 MCP Server 实现 |
| FR-DB-09 查询结果引用溯源 | ✅ 返回结果包含 db_name、query_time_ms 等元数据 |


---

## 七、部署与运维

### 7.1 启动命令

```bash
# 加载环境变量
source .env.mcp_db_server

# 启动 Database MCP Server
uv run mcp/server/db_mcp_server.py \
    --config conf/mcp_db_server.yaml \
    --host 0.0.0.0 \
    --port 9383
```

### 7.2 Docker 部署

```dockerfile
# Dockerfile.mcp_db_server
FROM python:3.12-slim

WORKDIR /app

# 安装依赖
COPY pyproject.toml uv.lock ./
RUN pip install uv && uv sync --frozen

# 复制代码
COPY mcp/ mcp/
COPY conf/ conf/
COPY common/ common/

# 环境变量
ENV MCP_DB_SERVER_CONFIG=/app/conf/mcp_db_server.yaml

EXPOSE 9383

CMD ["uv", "run", "mcp/server/db_mcp_server.py", "--config", "/app/conf/mcp_db_server.yaml"]
```

```yaml
# docker-compose.mcp_db_server.yml
version: '3.8'
services:
  mcp-db-server:
    build:
      context: ..
      dockerfile: docker/Dockerfile.mcp_db_server
    ports:
      - "9383:9383"
    env_file:
      - ../.env.mcp_db_server
    volumes:
      - ../conf/mcp_db_server.yaml:/app/conf/mcp_db_server.yaml:ro
      - mcp-logs:/var/log/mcp-db-server
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9383/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  mcp-logs:
```

### 7.3 监控指标（Prometheus）

| 指标名 | 类型 | 说明 |
|--------|------|------|
| `mcp_db_query_total` | Counter | 查询总次数（按 db_name、status 分组） |
| `mcp_db_query_duration_seconds` | Histogram | 查询耗时分布（按 db_name 分组） |
| `mcp_db_query_rows` | Histogram | 返回行数分布 |
| `mcp_db_connection_pool_active` | Gauge | 各数据库活跃连接数 |
| `mcp_db_connection_pool_idle` | Gauge | 各数据库空闲连接数 |
| `mcp_db_security_block_total` | Counter | 安全拦截次数（按拦截原因分组） |

### 7.4 健康检查端点

```
GET /health
```

响应示例：

```json
{
    "status": "healthy",
    "server": {
        "version": "1.0.0",
        "uptime": "2d 3h 15m"
    },
    "databases": {
        "erp-prod": {"status": "healthy", "db_type": "mysql", "pool_active": 3, "pool_idle": 7},
        "mes-prod": {"status": "healthy", "db_type": "mysql", "pool_active": 1, "pool_idle": 9},
        "qms-prod": {"status": "healthy", "db_type": "oracle", "pool_active": 2, "pool_idle": 8},
        "clean-db": {"status": "healthy", "db_type": "postgresql", "pool_active": 0, "pool_idle": 10},
        "plm-prod": {"status": "healthy", "db_type": "postgresql", "pool_active": 1, "pool_idle": 9},
        "wms-prod": {"status": "unhealthy", "db_type": "oracle", "error": "connection refused"}
    }
}
```

### 7.5 查询性能分析（FR-MCP-11）

MCP Server 提供查询性能分析能力，支持慢查询检测、执行计划分析和索引建议。

#### 7.5.1 慢查询检测

| 配置项 | 默认值 | 说明 |
|-------|-------|------|
| `slow_query_threshold_ms` | 5000 | 超过此阈值的查询记录为慢查询 |
| `slow_query_log_enabled` | true | 是否启用慢查询日志 |
| `slow_query_log_path` | `/var/log/mcp-db-server/slow_query.log` | 慢查询日志文件路径 |

**慢查询日志格式：**

```json
{
    "timestamp": "2026-07-25T14:30:00.123+08:00",
    "level": "WARN",
    "event": "slow_query_detected",
    "database": {"db_name": "erp-prod", "db_type": "mysql"},
    "query": {
        "sql": "SELECT ... FROM output_record WHERE ...",
        "execution_time_ms": 8234,
        "row_count": 1500
    },
    "caller": {"mcp_session_id": "abc123"}
}
```

#### 7.5.2 执行计划分析 Tool

提供 `analyze_query_{db_id}` 工具，返回 SQL 的执行计划而不实际执行查询：

```python
ANALYZE_QUERY_TOOL = types.Tool(
    name="analyze_query_{db_id}",
    description="分析 SQL 查询的执行计划，用于性能调优。不实际执行查询。",
    inputSchema={
        "type": "object",
        "properties": {
            "sql": {"type": "string", "description": "要分析的 SQL 查询语句"}
        },
        "required": ["sql"]
    }
)
```

**各数据库执行计划获取方式：**

| 数据库类型 | 执行计划语法 |
|-----------|------------|
| MySQL | `EXPLAIN FORMAT=JSON {sql}` |
| PostgreSQL | `EXPLAIN (FORMAT JSON, ANALYZE false) {sql}` |
| Oracle | `EXPLAIN PLAN FOR {sql}` + 查询 `PLAN_TABLE` |

#### 7.5.3 索引建议

基于执行计划分析，MCP Server 可输出索引建议（仅作为参考，不自动执行）：

```json
{
    "sql": "SELECT * FROM output_record WHERE factory_code = 'A' AND record_date > '2026-01-01'",
    "execution_plan": {...},
    "index_suggestions": [
        {
            "table": "output_record",
            "suggested_index": "CREATE INDEX idx_factory_date ON output_record(factory_code, record_date)",
            "reason": "查询条件包含 factory_code 和 record_date，建议创建复合索引"
        }
    ]
}
```


---

## 八、扩展性设计

### 8.1 新增数据库类型

只需 3 步：

1. **实现适配器**：继承 `BaseDBAdapter`，实现 `connect/execute_sql/get_schema/list_tables/close`
2. **注册到工厂**：在 `DBAdapterFactory._ADAPTER_MAP` 中添加映射
3. **添加配置**：在 `mcp_db_server.yaml` 的 `databases` 下新增配置项

Database Tool 层**无需任何修改**。

### 8.2 新增数据库实例

只需 1 步：

在 `mcp_db_server.yaml` 的 `databases` 下新增配置项，重启 MCP Server 即可。

### 8.3 从单实例拆分到多实例

如果未来数据库数量增长到需要隔离，可以按业务域拆分为多个 MCP Server：

```
Database MCP Server - 制造域（端口 9383）
  ├── erp-prod
  ├── mes-prod
  └── qms-prod

Database MCP Server - 数据域（端口 9384）
  ├── clean-db
  ├── plm-prod
  └── wms-prod
```

Database Tool 通过不同的 `mcp_server_id` 调用不同的 MCP Server，适配器代码无需修改。


---

## 九、依赖清单

| 依赖包 | 用途 | 版本要求 |
|--------|------|---------|
| `mcp` | MCP 协议 SDK | >= 1.0.0 |
| `pymysql` | MySQL 驱动 | >= 1.1.0 |
| `psycopg2-binary` | PostgreSQL 驱动 | >= 2.9.0 |
| `oracledb` | Oracle 驱动（Thin 模式） | >= 2.0.0 |
| `DBUtils` | 连接池管理 | >= 3.0.0 |
| `PyYAML` | 配置文件解析 | >= 6.0 |
| `prometheus-client` | 监控指标 | >= 0.20.0 |
| `starlette` | ASGI 框架（复用现有） | 与项目一致 |
| `uvicorn` | ASGI 服务器 | 与项目一致 |

---

*本文档为 Database MCP Server 的技术设计文档，与 [Database Tool PRD](./dbtoolprd.md) 配合使用。MCP Server 负责数据库连接与 SQL 执行，Database Tool 负责意图识别与 SQL 生成，两者通过 MCP 协议解耦协作。*
