-- ============================================================
--  SHOPY — Complete MySQL Schema
--  Run this in MySQL Workbench or via: mysql -u root -p < shopy_mysql.sql
-- ============================================================

CREATE DATABASE IF NOT EXISTS shopy CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE shopy;

-- ── USERS ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(80)  NOT NULL,
    phone_number  VARCHAR(30)  DEFAULT NULL,
    email         VARCHAR(120) NOT NULL UNIQUE,
    password      VARCHAR(256) NOT NULL,
    verified      TINYINT(1)   DEFAULT 0,
    role          VARCHAR(20)  DEFAULT 'customer',       -- 'customer' | 'retailer'
    profile_pic   TEXT         DEFAULT NULL,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_users_email  (email),
    INDEX idx_users_phone  (phone_number)
) ENGINE=InnoDB;

-- ── CATEGORIES ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS categories (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    name       VARCHAR(100) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- Seed categories
INSERT IGNORE INTO categories (name) VALUES
    ('Electronics'),
    ('Fashion'),
    ('Home & Garden'),
    ('Sports'),
    ('Books'),
    ('Beauty'),
    ('Toys'),
    ('Food'),
    ('Health'),
    ('Automobiles');

-- ── PRODUCTS ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS products (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    retailer_id    INT           NOT NULL,
    category_id    INT           DEFAULT NULL,
    name           VARCHAR(200)  NOT NULL,
    description    TEXT,
    price          DECIMAL(10,2) NOT NULL,
    original_price DECIMAL(10,2) DEFAULT NULL,
    stock          INT           DEFAULT 0,
    image_url      TEXT          DEFAULT NULL,
    is_active      TINYINT(1)    DEFAULT 1,
    created_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (retailer_id)  REFERENCES users(id)       ON DELETE CASCADE,
    FOREIGN KEY (category_id)  REFERENCES categories(id)  ON DELETE SET NULL,
    INDEX idx_prod_retailer  (retailer_id),
    INDEX idx_prod_category  (category_id),
    INDEX idx_prod_active    (is_active)
) ENGINE=InnoDB;

-- ── PRODUCT IMAGES (gallery) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS product_images (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    product_id  INT  NOT NULL,
    image_url   TEXT NOT NULL,
    is_primary  TINYINT(1) DEFAULT 0,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_pi_product (product_id)
) ENGINE=InnoDB;

-- ── CART ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS cart (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    product_id  INT NOT NULL,
    quantity    INT DEFAULT 1,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_cart (user_id, product_id),
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_cart_user (user_id)
) ENGINE=InnoDB;

-- ── WISHLISTS ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wishlists (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT NOT NULL,
    product_id  INT NOT NULL,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_wishlist (user_id, product_id),
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_wish_user (user_id)
) ENGINE=InnoDB;

-- ── ORDERS ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS orders (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    customer_id      INT           NOT NULL,
    total_amount     DECIMAL(10,2) NOT NULL,
    status           VARCHAR(30)   DEFAULT 'pending',
                                   -- pending | processing | shipped | delivered | cancelled
    shipping_name    VARCHAR(100)  DEFAULT NULL,
    shipping_address TEXT          DEFAULT NULL,
    shipping_phone   VARCHAR(30)   DEFAULT NULL,
    notes            TEXT          DEFAULT NULL,
    created_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP     DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_order_customer (customer_id),
    INDEX idx_order_status   (status)
) ENGINE=InnoDB;

-- ── ORDER ITEMS ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS order_items (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    order_id     INT           NOT NULL,
    product_id   INT           DEFAULT NULL,
    retailer_id  INT           DEFAULT NULL,
    product_name VARCHAR(200)  DEFAULT NULL,
    quantity     INT           NOT NULL,
    unit_price   DECIMAL(10,2) NOT NULL,
    FOREIGN KEY (order_id)    REFERENCES orders(id)   ON DELETE CASCADE,
    FOREIGN KEY (product_id)  REFERENCES products(id) ON DELETE SET NULL,
    FOREIGN KEY (retailer_id) REFERENCES users(id)    ON DELETE SET NULL,
    INDEX idx_oi_order   (order_id),
    INDEX idx_oi_retailer(retailer_id)
) ENGINE=InnoDB;

-- ── REVIEWS ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS reviews (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    product_id  INT  NOT NULL,
    user_id     INT  NOT NULL,
    rating      INT  NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment     TEXT DEFAULT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_review (product_id, user_id),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id)    REFERENCES users(id)    ON DELETE CASCADE,
    INDEX idx_rev_product (product_id)
) ENGINE=InnoDB;

-- ── DISCOUNT CODES ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS discount_codes (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(50)   NOT NULL UNIQUE,
    discount_type   VARCHAR(20)   DEFAULT 'percent',   -- 'percent' | 'fixed'
    discount_value  DECIMAL(10,2) NOT NULL,
    min_order_value DECIMAL(10,2) DEFAULT 0,
    max_uses        INT           DEFAULT NULL,
    uses_count      INT           DEFAULT 0,
    expires_at      TIMESTAMP     DEFAULT NULL,
    is_active       TINYINT(1)    DEFAULT 1,
    created_at      TIMESTAMP     DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB;

-- ── ADDRESSES ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS addresses (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT          NOT NULL,
    label        VARCHAR(50)  DEFAULT 'Home',
    full_name    VARCHAR(100) DEFAULT NULL,
    phone        VARCHAR(30)  DEFAULT NULL,
    address_line TEXT         NOT NULL,
    city         VARCHAR(100) DEFAULT NULL,
    province     VARCHAR(100) DEFAULT NULL,
    postal_code  VARCHAR(20)  DEFAULT NULL,
    is_default   TINYINT(1)   DEFAULT 0,
    created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_addr_user (user_id)
) ENGINE=InnoDB;

-- ── NOTIFICATIONS ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS notifications (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT         NOT NULL,
    message    TEXT        NOT NULL,
    type       VARCHAR(50) DEFAULT 'info',    -- 'order' | 'promo' | 'info'
    is_read    TINYINT(1)  DEFAULT 0,
    created_at TIMESTAMP   DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_notif_user (user_id)
) ENGINE=InnoDB;

-- ============================================================
-- HOW TO USE:
--   1. Open MySQL Workbench (or any MySQL client)
--   2. Run: source /path/to/shopy_mysql.sql
--      OR:   mysql -u root -p < shopy_mysql.sql
--   3. Update your .env file:
--        DB_HOST=localhost
--        DB_USER=root
--        DB_PASSWORD=yourpassword
--        DB_NAME=shopy
-- ============================================================
