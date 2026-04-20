# Phase 1 Instructions (CS-254 Final Project)

This guide tells you exactly how to complete Phase 1 using [shopy.sql](shopy.sql).

## 1) What You Now Have

- A Phase-1-ready schema in [shopy.sql](shopy.sql)
- Normalized relational design with PK/FK constraints
- Existing schema preserved and extended (no breaking removals)
- Added missing e-commerce tables for stronger coverage: `payments`, `shipments`
- Realistic seed data included (10+ rows in every table)

## 2) Run the Database Script

Use one of these options.

## Option A: MySQL CLI

```bash
mysql -u root -p < shopy.sql
```

## Option B: MySQL Workbench

1. Open MySQL Workbench.
2. Open [shopy.sql](shopy.sql).
3. Execute all statements.

## 3) Verify Manual Requirements with SQL

After running the script, execute these checks.

## A. Row count check (must be >= 10 for each table)

```sql
SELECT 'users' AS table_name, COUNT(*) AS rows_count FROM users
UNION ALL SELECT 'categories', COUNT(*) FROM categories
UNION ALL SELECT 'products', COUNT(*) FROM products
UNION ALL SELECT 'product_images', COUNT(*) FROM product_images
UNION ALL SELECT 'discount_codes', COUNT(*) FROM discount_codes
UNION ALL SELECT 'addresses', COUNT(*) FROM addresses
UNION ALL SELECT 'orders', COUNT(*) FROM orders
UNION ALL SELECT 'order_items', COUNT(*) FROM order_items
UNION ALL SELECT 'cart', COUNT(*) FROM cart
UNION ALL SELECT 'wishlists', COUNT(*) FROM wishlists
UNION ALL SELECT 'reviews', COUNT(*) FROM reviews
UNION ALL SELECT 'ai_chat_history', COUNT(*) FROM ai_chat_history
UNION ALL SELECT 'notifications', COUNT(*) FROM notifications
UNION ALL SELECT 'payments', COUNT(*) FROM payments
UNION ALL SELECT 'shipments', COUNT(*) FROM shipments;
```

## B. Check each table has at least 4 non-PK columns

```sql
SELECT
  table_name,
  SUM(CASE WHEN column_key = 'PRI' THEN 0 ELSE 1 END) AS non_pk_columns
FROM information_schema.columns
WHERE table_schema = 'shopy'
GROUP BY table_name
ORDER BY non_pk_columns;
```

If any table shows `< 4`, update that table before submission.

## C. Check foreign keys exist

```sql
SELECT
  table_name,
  COUNT(*) AS fk_count
FROM information_schema.key_column_usage
WHERE table_schema = 'shopy'
  AND referenced_table_name IS NOT NULL
GROUP BY table_name
ORDER BY fk_count DESC, table_name;
```

## D. Multi-table join sanity check (dynamic data behavior proof)

```sql
SELECT
  o.id AS order_id,
  u.username AS customer,
  o.status,
  SUM(oi.quantity * (oi.unit_price - oi.discount_per_unit)) AS computed_total
FROM orders o
JOIN users u ON u.id = o.customer_id
JOIN order_items oi ON oi.order_id = o.id
GROUP BY o.id, u.username, o.status
ORDER BY o.id;
```

## 4) Build the ERD (Required)

You must include ERD in report.

## Recommended workflow

1. Use MySQL Workbench reverse engineer, or draw it manually in draw.io.
2. Include all tables from [shopy.sql](shopy.sql).
3. Show for each table:
   - Columns
   - PK
   - FK
   - Cardinality (1-to-many or many-to-many via junction table)
4. Export as high-resolution PNG/PDF.

## 5) Build Data Dictionary (Required)

Create one section per table, and for every column include:
- Column name
- Data type
- PK/FK status
- Constraints (`NOT NULL`, `UNIQUE`, `DEFAULT`, etc.)
- One-sentence meaning

Use this helper query to generate structure quickly:

```sql
SELECT
  TABLE_NAME,
  COLUMN_NAME,
  COLUMN_TYPE,
  IS_NULLABLE,
  COLUMN_KEY,
  COLUMN_DEFAULT,
  EXTRA
FROM information_schema.columns
WHERE table_schema = 'shopy'
ORDER BY TABLE_NAME, ORDINAL_POSITION;
```

And FK mapping query:

```sql
SELECT
  TABLE_NAME,
  COLUMN_NAME,
  REFERENCED_TABLE_NAME,
  REFERENCED_COLUMN_NAME
FROM information_schema.key_column_usage
WHERE table_schema = 'shopy'
  AND referenced_table_name IS NOT NULL
ORDER BY TABLE_NAME, COLUMN_NAME;
```

## 6) Write Report Sections for Phase 1

Your report must include:

1. Domain justification paragraph (why e-commerce needs this structure).
2. ERD image.
3. Full data dictionary.
4. Relationship explanation and cardinalities.
5. 3NF explanation with examples from your schema:
   - 1NF: atomic columns
   - 2NF: full dependency on PK
   - 3NF: no transitive dependency among non-key attributes

## 7) Phase 1 Final Checklist

Before submission, confirm all are true:

- [ ] At least 7 to 8 related tables (you now have 14)
- [ ] At least 7 to 8 related tables (you now have 15)
- [ ] Each table has >= 4 meaningful non-PK columns
- [ ] PK on every table
- [ ] Proper FK constraints present
- [ ] At least one junction table (you have `order_items`, `cart`, `wishlists`)
- [ ] At least 10 realistic rows per table
- [ ] ERD exported and included
- [ ] Data dictionary completed
- [ ] 3NF explanation written

## 8) Important Note for Your Existing App

This schema keeps your existing structure and app-compatible columns intact.
Extra columns/tables were added with defaults so current inserts still work.
