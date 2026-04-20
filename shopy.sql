-- ============================================================
-- SHOPY - PHASE 1 READY SQL (CS-254 Final Project)
-- Domain: E-Commerce
-- Goal: 3NF-friendly relational schema + realistic seed data
-- ============================================================

CREATE DATABASE IF NOT EXISTS shopy CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE shopy;
SET NAMES utf8mb4;

-- ============================================================
-- 1) CORE TABLES (Normalized, with PK/FK and useful indexes)
-- ============================================================

CREATE TABLE IF NOT EXISTS users (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    username      VARCHAR(80)  NOT NULL,
    phone_number  VARCHAR(30)  DEFAULT NULL,
    email         VARCHAR(120) NOT NULL UNIQUE,
    password      VARCHAR(256) NOT NULL,
    verified      TINYINT(1)   NOT NULL DEFAULT 0,
    role          VARCHAR(20)  NOT NULL DEFAULT 'customer',
    profile_pic   TEXT         DEFAULT NULL,
    bio           TEXT         DEFAULT NULL,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_users_email (email),
    INDEX idx_users_phone (phone_number),
    INDEX idx_users_role (role)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS categories (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    name          VARCHAR(100) NOT NULL,
    slug          VARCHAR(120) DEFAULT NULL,
    description   VARCHAR(255) DEFAULT NULL,
    is_active     TINYINT(1)   NOT NULL DEFAULT 1,
    created_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_categories_name (name),
    UNIQUE KEY uq_categories_slug (slug),
    INDEX idx_categories_active (is_active)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS products (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    retailer_id    INT           NOT NULL,
    category_id    INT           DEFAULT NULL,
    name           VARCHAR(200)  NOT NULL,
    description    TEXT,
    price          DECIMAL(10,2) NOT NULL,
    original_price DECIMAL(10,2) DEFAULT NULL,
    stock          INT           NOT NULL DEFAULT 0,
    image_url      TEXT          DEFAULT NULL,
    sku            VARCHAR(80)   DEFAULT NULL,
    weight_grams   INT           DEFAULT NULL,
    is_active      TINYINT(1)    NOT NULL DEFAULT 1,
    created_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (retailer_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL,
    UNIQUE KEY uq_products_sku (sku),
    INDEX idx_prod_retailer (retailer_id),
    INDEX idx_prod_category (category_id),
    INDEX idx_prod_active (is_active)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS product_images (
    id                  INT AUTO_INCREMENT PRIMARY KEY,
    product_id          INT          NOT NULL,
    image_url           TEXT         NOT NULL,
    alt_text            VARCHAR(180) DEFAULT NULL,
    sort_order          INT          NOT NULL DEFAULT 1,
    is_primary          TINYINT(1)   NOT NULL DEFAULT 0,
    uploaded_by_user_id INT          DEFAULT NULL,
    created_at          TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (uploaded_by_user_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_pi_product (product_id),
    INDEX idx_pi_primary (product_id, is_primary)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS discount_codes (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    code            VARCHAR(50)   NOT NULL,
    campaign_name   VARCHAR(120)  DEFAULT NULL,
    discount_type   VARCHAR(20)   NOT NULL DEFAULT 'percent',
    discount_value  DECIMAL(10,2) NOT NULL,
    min_order_value DECIMAL(10,2) NOT NULL DEFAULT 0,
    max_uses        INT           DEFAULT NULL,
    uses_count      INT           NOT NULL DEFAULT 0,
    expires_at      DATETIME      DEFAULT NULL,
    is_active       TINYINT(1)    NOT NULL DEFAULT 1,
    created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_discount_code (code),
    INDEX idx_dc_active (is_active)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS addresses (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    user_id      INT          NOT NULL,
    label        VARCHAR(50)  NOT NULL DEFAULT 'Home',
    full_name    VARCHAR(100) DEFAULT NULL,
    phone        VARCHAR(30)  DEFAULT NULL,
    address_line TEXT         NOT NULL,
    city         VARCHAR(100) DEFAULT NULL,
    province     VARCHAR(100) DEFAULT NULL,
    postal_code  VARCHAR(20)  DEFAULT NULL,
    country      VARCHAR(100) NOT NULL DEFAULT 'Pakistan',
    is_default   TINYINT(1)   NOT NULL DEFAULT 0,
    created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_addr_user (user_id),
    INDEX idx_addr_default (user_id, is_default)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS orders (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    customer_id      INT           NOT NULL,
    total_amount     DECIMAL(10,2) NOT NULL,
    status           VARCHAR(30)   NOT NULL DEFAULT 'pending',
    shipping_name    VARCHAR(100)  DEFAULT NULL,
    shipping_address TEXT          DEFAULT NULL,
    shipping_phone   VARCHAR(30)   DEFAULT NULL,
    notes            TEXT          DEFAULT NULL,
    discount_code_id INT           DEFAULT NULL,
    discount_amount  DECIMAL(10,2) NOT NULL DEFAULT 0,
    payment_method   VARCHAR(30)   NOT NULL DEFAULT 'cod',
    payment_status   VARCHAR(20)   NOT NULL DEFAULT 'pending',
    payment_ref      VARCHAR(64)   DEFAULT NULL,
    created_at       TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (customer_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (discount_code_id) REFERENCES discount_codes(id) ON DELETE SET NULL,
    INDEX idx_order_customer (customer_id),
    INDEX idx_order_status (status),
    INDEX idx_order_payment_status (payment_status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS order_items (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    order_id          INT           NOT NULL,
    product_id        INT           DEFAULT NULL,
    retailer_id       INT           DEFAULT NULL,
    product_name      VARCHAR(200)  DEFAULT NULL,
    quantity          INT           NOT NULL,
    unit_price        DECIMAL(10,2) NOT NULL,
    discount_per_unit DECIMAL(10,2) NOT NULL DEFAULT 0,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE SET NULL,
    FOREIGN KEY (retailer_id) REFERENCES users(id) ON DELETE SET NULL,
    INDEX idx_oi_order (order_id),
    INDEX idx_oi_product (product_id),
    INDEX idx_oi_retailer (retailer_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS cart (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT        NOT NULL,
    product_id  INT        NOT NULL,
    quantity    INT        NOT NULL DEFAULT 1,
    added_at    TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP  NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_cart_user_product (user_id, product_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_cart_user (user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS wishlists (
    id                    INT AUTO_INCREMENT PRIMARY KEY,
    user_id               INT           NOT NULL,
    product_id            INT           NOT NULL,
    priority              TINYINT       NOT NULL DEFAULT 3,
    target_price          DECIMAL(10,2) DEFAULT NULL,
    notify_on_price_drop  TINYINT(1)    NOT NULL DEFAULT 0,
    note                  VARCHAR(255)  DEFAULT NULL,
    added_at              TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (priority BETWEEN 1 AND 5),
    UNIQUE KEY uq_wishlist_user_product (user_id, product_id),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    INDEX idx_wishlist_user (user_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS reviews (
    id                   INT AUTO_INCREMENT PRIMARY KEY,
    product_id           INT          NOT NULL,
    user_id              INT          NOT NULL,
    rating               INT          NOT NULL,
    title                VARCHAR(120) DEFAULT NULL,
    comment              TEXT         DEFAULT NULL,
    is_verified_purchase TINYINT(1)   NOT NULL DEFAULT 0,
    created_at           TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (rating BETWEEN 1 AND 5),
    UNIQUE KEY uq_review_product_user (product_id, user_id),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_review_product (product_id)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS ai_chat_history (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    user_id    INT NOT NULL,
    role       VARCHAR(20) NOT NULL DEFAULT 'customer',
    sender     ENUM('user','bot') NOT NULL,
    message    TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_ach_user (user_id),
    INDEX idx_ach_created (user_id, created_at)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS notifications (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    user_id     INT          NOT NULL,
    message     TEXT         NOT NULL,
    type        VARCHAR(50)  NOT NULL DEFAULT 'info',
    channel     VARCHAR(30)  NOT NULL DEFAULT 'in_app',
    action_url  VARCHAR(255) DEFAULT NULL,
    is_read     TINYINT(1)   NOT NULL DEFAULT 0,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    INDEX idx_notif_user (user_id),
    INDEX idx_notif_read (user_id, is_read)
) ENGINE=InnoDB;

-- Added for Phase-1 completeness in e-commerce domain
CREATE TABLE IF NOT EXISTS payments (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    order_id        INT           NOT NULL,
    payment_method  VARCHAR(30)   NOT NULL,
    provider        VARCHAR(40)   DEFAULT NULL,
    amount_paid     DECIMAL(10,2) NOT NULL,
    payment_status  VARCHAR(20)   NOT NULL DEFAULT 'pending',
    transaction_ref VARCHAR(120)  DEFAULT NULL,
    paid_at         DATETIME      DEFAULT NULL,
    created_at      TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    UNIQUE KEY uq_payment_order_txn (order_id, transaction_ref),
    INDEX idx_payment_status (payment_status)
) ENGINE=InnoDB;

-- Added for Phase-1 completeness in e-commerce domain
CREATE TABLE IF NOT EXISTS shipments (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    order_id          INT           NOT NULL,
    courier_name      VARCHAR(80)   NOT NULL,
    tracking_number   VARCHAR(120)  DEFAULT NULL,
    shipment_status   VARCHAR(20)   NOT NULL DEFAULT 'pending',
    shipped_at        DATETIME      DEFAULT NULL,
    expected_delivery DATETIME      DEFAULT NULL,
    delivered_at      DATETIME      DEFAULT NULL,
    shipping_cost     DECIMAL(10,2) NOT NULL DEFAULT 0,
    created_at        TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(id) ON DELETE CASCADE,
    UNIQUE KEY uq_shipment_tracking (tracking_number),
    INDEX idx_shipment_status (shipment_status)
) ENGINE=InnoDB;

-- ============================================================
-- 2) REALISTIC SAMPLE DATA (>= 10 rows per table)
-- ============================================================

INSERT INTO users
(id, username, phone_number, email, password, verified, role, profile_pic, bio, created_at, updated_at)
VALUES
(1, 'ahmad_electro', '+923001000001', 'ahmad@shopy.pk', 'pbkdf2:sha256:260000$shopy$demo_hash_value_1', 1, 'retailer', '/static/uploads/profile_pics/ahmad.jpg', 'Retailer focused on gadgets and electronics.', '2026-01-01 10:00:00', '2026-01-01 10:00:00'),
(2, 'sara_style', '+923001000002', 'sara@shopy.pk', 'pbkdf2:sha256:260000$shopy$demo_hash_value_2', 1, 'retailer', '/static/uploads/profile_pics/sara.jpg', 'Fashion store owner with seasonal collections.', '2026-01-01 10:05:00', '2026-01-01 10:05:00'),
(3, 'bilal_home', '+923001000003', 'bilal@shopy.pk', 'pbkdf2:sha256:260000$shopy$demo_hash_value_3', 1, 'retailer', '/static/uploads/profile_pics/bilal.jpg', 'Home and kitchen product specialist.', '2026-01-01 10:10:00', '2026-01-01 10:10:00'),
(4, 'hina_sports', '+923001000004', 'hina@shopy.pk', 'pbkdf2:sha256:260000$shopy$demo_hash_value_4', 1, 'retailer', '/static/uploads/profile_pics/hina.jpg', 'Sports accessories and fitness gear seller.', '2026-01-01 10:15:00', '2026-01-01 10:15:00'),
(5, 'omar_books', '+923001000005', 'omar@shopy.pk', 'pbkdf2:sha256:260000$shopy$demo_hash_value_5', 1, 'retailer', '/static/uploads/profile_pics/omar.jpg', 'Books and stationery retailer.', '2026-01-01 10:20:00', '2026-01-01 10:20:00'),
(6, 'ali_khan', '+923001000006', 'ali.khan@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_6', 1, 'customer', NULL, 'Prefers electronics and audio products.', '2026-01-02 09:00:00', '2026-01-02 09:00:00'),
(7, 'aisha_noor', '+923001000007', 'aisha.noor@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_7', 1, 'customer', NULL, 'Buys skincare and fashion products.', '2026-01-03 09:15:00', '2026-01-03 09:15:00'),
(8, 'zain_ahmed', '+923001000008', 'zain.ahmed@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_8', 1, 'customer', NULL, 'Active fitness and sports buyer.', '2026-01-04 11:00:00', '2026-01-04 11:00:00'),
(9, 'maryam_ali', '+923001000009', 'maryam.ali@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_9', 1, 'customer', NULL, 'Home decor and kitchen enthusiast.', '2026-01-05 12:00:00', '2026-01-05 12:00:00'),
(10, 'hassan_raza', '+923001000010', 'hassan.raza@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_10', 1, 'customer', NULL, 'Reads productivity and technical books.', '2026-01-06 13:00:00', '2026-01-06 13:00:00'),
(11, 'noor_fatima', '+923001000011', 'noor.fatima@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_11', 1, 'customer', NULL, 'Health and wellness focused shopper.', '2026-01-07 14:00:00', '2026-01-07 14:00:00'),
(12, 'usman_saeed', '+923001000012', 'usman.saeed@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_12', 1, 'customer', NULL, 'Looks for value deals and bundle offers.', '2026-01-08 15:00:00', '2026-01-08 15:00:00'),
(13, 'komal_sheikh', '+923001000013', 'komal.sheikh@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_13', 1, 'customer', NULL, 'Interested in gifts and children products.', '2026-01-09 16:00:00', '2026-01-09 16:00:00'),
(14, 'faizan_habib', '+923001000014', 'faizan.habib@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_14', 1, 'customer', NULL, 'Frequent buyer of auto accessories.', '2026-01-10 17:00:00', '2026-01-10 17:00:00'),
(15, 'maham_asif', '+923001000015', 'maham.asif@gmail.com', 'pbkdf2:sha256:260000$shopy$demo_hash_value_15', 1, 'customer', NULL, 'Orders premium lifestyle products.', '2026-01-11 18:00:00', '2026-01-11 18:00:00')
ON DUPLICATE KEY UPDATE
username = VALUES(username),
phone_number = VALUES(phone_number),
email = VALUES(email),
password = VALUES(password),
verified = VALUES(verified),
role = VALUES(role),
profile_pic = VALUES(profile_pic),
bio = VALUES(bio),
updated_at = VALUES(updated_at);

INSERT INTO categories
(id, name, slug, description, is_active, created_at, updated_at)
VALUES
(1, 'Electronics', 'electronics', 'Smart devices, audio gear, and accessories.', 1, '2026-01-01 09:00:00', '2026-01-01 09:00:00'),
(2, 'Fashion', 'fashion', 'Clothing and wearable lifestyle products.', 1, '2026-01-01 09:01:00', '2026-01-01 09:01:00'),
(3, 'Home and Garden', 'home-garden', 'Kitchenware, decor, and daily living products.', 1, '2026-01-01 09:02:00', '2026-01-01 09:02:00'),
(4, 'Sports', 'sports', 'Fitness and sports equipment for active users.', 1, '2026-01-01 09:03:00', '2026-01-01 09:03:00'),
(5, 'Books', 'books', 'Books, journals, and learning resources.', 1, '2026-01-01 09:04:00', '2026-01-01 09:04:00'),
(6, 'Beauty', 'beauty', 'Skincare and beauty essentials.', 1, '2026-01-01 09:05:00', '2026-01-01 09:05:00'),
(7, 'Toys', 'toys', 'Children toys and educational kits.', 1, '2026-01-01 09:06:00', '2026-01-01 09:06:00'),
(8, 'Food', 'food', 'Tea, snacks, and pantry products.', 1, '2026-01-01 09:07:00', '2026-01-01 09:07:00'),
(9, 'Health', 'health', 'Health supplements and wellness items.', 1, '2026-01-01 09:08:00', '2026-01-01 09:08:00'),
(10, 'Automobiles', 'automobiles', 'Auto-care and automotive accessories.', 1, '2026-01-01 09:09:00', '2026-01-01 09:09:00')
ON DUPLICATE KEY UPDATE
name = VALUES(name),
slug = VALUES(slug),
description = VALUES(description),
is_active = VALUES(is_active),
updated_at = VALUES(updated_at);

INSERT INTO products
(id, retailer_id, category_id, name, description, price, original_price, stock, image_url, sku, weight_grams, is_active, created_at, updated_at)
VALUES
(1, 1, 1, 'Noise Cancelling Headphones', 'Over-ear wireless headphones with active noise cancellation.', 18500.00, 22000.00, 24, '/static/uploads/products/p1.jpg', 'ELEC-HP-1001', 260, 1, '2026-01-12 10:00:00', '2026-01-12 10:00:00'),
(2, 1, 1, '4K Action Camera', 'Water-resistant action camera with stabilization.', 27999.00, 31999.00, 12, '/static/uploads/products/p2.jpg', 'ELEC-CAM-1002', 190, 1, '2026-01-13 10:00:00', '2026-01-13 10:00:00'),
(3, 1, 1, 'Mechanical Keyboard', 'RGB mechanical keyboard with hot-swappable switches.', 16500.00, 18500.00, 18, '/static/uploads/products/p3.jpg', 'ELEC-KB-1003', 820, 1, '2026-01-14 10:00:00', '2026-01-14 10:00:00'),
(4, 1, 4, 'Smart Fitness Watch', 'Fitness watch with heart-rate and sleep tracking.', 14900.00, 16900.00, 20, '/static/uploads/products/p4.jpg', 'SPRT-WAT-1004', 75, 1, '2026-01-15 10:00:00', '2026-01-15 10:00:00'),
(5, 2, 2, 'Women Linen Kurta', 'Breathable linen kurta with modern cut.', 7490.00, 8990.00, 40, '/static/uploads/products/p5.jpg', 'FSHN-KRT-2001', 320, 1, '2026-01-16 10:00:00', '2026-01-16 10:00:00'),
(6, 2, 2, 'Men Denim Jacket', 'Classic fit denim jacket with premium stitching.', 8900.00, 10900.00, 25, '/static/uploads/products/p6.jpg', 'FSHN-JKT-2002', 640, 1, '2026-01-17 10:00:00', '2026-01-17 10:00:00'),
(7, 4, 4, 'Running Shoes Pro', 'Cushioned running shoes for long-distance training.', 12800.00, 14900.00, 30, '/static/uploads/products/p7.jpg', 'SPRT-SHO-4001', 540, 1, '2026-01-18 10:00:00', '2026-01-18 10:00:00'),
(8, 4, 4, 'Yoga Mat Anti-Slip', 'High-grip anti-slip yoga mat with carry strap.', 2990.00, 3490.00, 55, '/static/uploads/products/p8.jpg', 'SPRT-MAT-4002', 980, 1, '2026-01-19 10:00:00', '2026-01-19 10:00:00'),
(9, 3, 3, 'Air Fryer 6L', '6-liter digital air fryer for healthy cooking.', 29999.00, 34999.00, 14, '/static/uploads/products/p9.jpg', 'HOME-AFR-3001', 5400, 1, '2026-01-20 10:00:00', '2026-01-20 10:00:00'),
(10, 3, 3, 'Nonstick Cookware Set', '10-piece nonstick cookware set for daily use.', 15800.00, 18900.00, 17, '/static/uploads/products/p10.jpg', 'HOME-CKW-3002', 4200, 1, '2026-01-21 10:00:00', '2026-01-21 10:00:00'),
(11, 3, 8, 'Organic Green Tea 100g', 'Premium loose-leaf organic green tea.', 1450.00, 1700.00, 80, '/static/uploads/products/p11.jpg', 'FOOD-TEA-3003', 110, 1, '2026-01-22 10:00:00', '2026-01-22 10:00:00'),
(12, 2, 6, 'Vitamin C Serum', 'Brightening serum with stable vitamin C formulation.', 3200.00, 3900.00, 60, '/static/uploads/products/p12.jpg', 'BEAU-SRM-2003', 70, 1, '2026-01-23 10:00:00', '2026-01-23 10:00:00'),
(13, 1, 7, 'STEM Robot Kit', 'Educational programmable robot kit for kids.', 11200.00, 12900.00, 22, '/static/uploads/products/p13.jpg', 'TOYS-ROB-1005', 1300, 1, '2026-01-24 10:00:00', '2026-01-24 10:00:00'),
(14, 1, 10, 'Car Vacuum Cleaner', 'Portable high-suction vacuum for car interior.', 7800.00, 9200.00, 27, '/static/uploads/products/p14.jpg', 'AUTO-VAC-1006', 1250, 1, '2026-01-25 10:00:00', '2026-01-25 10:00:00'),
(15, 5, 5, 'Productivity Planner 2026', 'Goal-based daily planner with monthly review pages.', 1800.00, 2200.00, 70, '/static/uploads/products/p15.jpg', 'BOOK-PLN-5001', 460, 1, '2026-01-26 10:00:00', '2026-01-26 10:00:00'),
(16, 5, 5, 'Python for Data Analysis', 'Practical data analytics guide for intermediate learners.', 2900.00, 3400.00, 45, '/static/uploads/products/p16.jpg', 'BOOK-PYT-5002', 520, 1, '2026-01-27 10:00:00', '2026-01-27 10:00:00'),
(17, 4, 4, 'Resistance Band Set', 'Set of 5 resistance bands with door anchor.', 4500.00, 5200.00, 38, '/static/uploads/products/p17.jpg', 'SPRT-BND-4003', 690, 1, '2026-01-28 10:00:00', '2026-01-28 10:00:00'),
(18, 1, 1, 'Bluetooth Portable Speaker', 'Portable speaker with deep bass and 12-hour battery.', 12500.00, 14500.00, 33, '/static/uploads/products/p18.jpg', 'ELEC-SPK-1007', 780, 1, '2026-01-29 10:00:00', '2026-01-29 10:00:00'),
(19, 3, 3, 'Ceramic Indoor Planter', 'Minimal ceramic planter for indoor spaces.', 2100.00, 2600.00, 50, '/static/uploads/products/p19.jpg', 'HOME-PLN-3004', 900, 1, '2026-01-30 10:00:00', '2026-01-30 10:00:00'),
(20, 3, 9, 'Protein Peanut Butter', 'High-protein peanut butter, no added sugar.', 4990.00, 5900.00, 44, '/static/uploads/products/p20.jpg', 'HLTH-PNB-3005', 1000, 1, '2026-01-31 10:00:00', '2026-01-31 10:00:00')
ON DUPLICATE KEY UPDATE
retailer_id = VALUES(retailer_id),
category_id = VALUES(category_id),
name = VALUES(name),
description = VALUES(description),
price = VALUES(price),
original_price = VALUES(original_price),
stock = VALUES(stock),
image_url = VALUES(image_url),
sku = VALUES(sku),
weight_grams = VALUES(weight_grams),
is_active = VALUES(is_active),
updated_at = VALUES(updated_at);

INSERT INTO product_images
(id, product_id, image_url, alt_text, sort_order, is_primary, uploaded_by_user_id, created_at)
VALUES
(1, 1, '/static/uploads/products/p1_1.jpg', 'Headphones front view', 1, 1, 1, '2026-02-01 10:00:00'),
(2, 2, '/static/uploads/products/p2_1.jpg', 'Action camera product shot', 1, 1, 1, '2026-02-01 10:01:00'),
(3, 3, '/static/uploads/products/p3_1.jpg', 'Mechanical keyboard angled shot', 1, 1, 1, '2026-02-01 10:02:00'),
(4, 4, '/static/uploads/products/p4_1.jpg', 'Fitness watch display view', 1, 1, 1, '2026-02-01 10:03:00'),
(5, 5, '/static/uploads/products/p5_1.jpg', 'Linen kurta front style', 1, 1, 2, '2026-02-01 10:04:00'),
(6, 6, '/static/uploads/products/p6_1.jpg', 'Denim jacket product image', 1, 1, 2, '2026-02-01 10:05:00'),
(7, 7, '/static/uploads/products/p7_1.jpg', 'Running shoes side profile', 1, 1, 4, '2026-02-01 10:06:00'),
(8, 8, '/static/uploads/products/p8_1.jpg', 'Yoga mat rolled and flat', 1, 1, 4, '2026-02-01 10:07:00'),
(9, 9, '/static/uploads/products/p9_1.jpg', 'Air fryer kitchen setup', 1, 1, 3, '2026-02-01 10:08:00'),
(10, 10, '/static/uploads/products/p10_1.jpg', 'Cookware set in use', 1, 1, 3, '2026-02-01 10:09:00'),
(11, 11, '/static/uploads/products/p11_1.jpg', 'Organic green tea pack', 1, 1, 3, '2026-02-01 10:10:00'),
(12, 12, '/static/uploads/products/p12_1.jpg', 'Vitamin C serum bottle', 1, 1, 2, '2026-02-01 10:11:00'),
(13, 13, '/static/uploads/products/p13_1.jpg', 'STEM robot kit package', 1, 1, 1, '2026-02-01 10:12:00'),
(14, 14, '/static/uploads/products/p14_1.jpg', 'Car vacuum cleaner set', 1, 1, 1, '2026-02-01 10:13:00'),
(15, 15, '/static/uploads/products/p15_1.jpg', 'Planner cover and pages', 1, 1, 5, '2026-02-01 10:14:00'),
(16, 16, '/static/uploads/products/p16_1.jpg', 'Python book cover', 1, 1, 5, '2026-02-01 10:15:00'),
(17, 17, '/static/uploads/products/p17_1.jpg', 'Resistance band set layout', 1, 1, 4, '2026-02-01 10:16:00'),
(18, 18, '/static/uploads/products/p18_1.jpg', 'Portable speaker top view', 1, 1, 1, '2026-02-01 10:17:00'),
(19, 19, '/static/uploads/products/p19_1.jpg', 'Ceramic planter indoor setup', 1, 1, 3, '2026-02-01 10:18:00'),
(20, 20, '/static/uploads/products/p20_1.jpg', 'Protein peanut butter jar', 1, 1, 3, '2026-02-01 10:19:00')
ON DUPLICATE KEY UPDATE
product_id = VALUES(product_id),
image_url = VALUES(image_url),
alt_text = VALUES(alt_text),
sort_order = VALUES(sort_order),
is_primary = VALUES(is_primary),
uploaded_by_user_id = VALUES(uploaded_by_user_id);

INSERT INTO discount_codes
(id, code, campaign_name, discount_type, discount_value, min_order_value, max_uses, uses_count, expires_at, is_active, created_at)
VALUES
(1, 'WELCOME10', 'New User Welcome', 'percent', 10.00, 3000.00, 500, 108, '2027-12-31 23:59:59', 1, '2026-01-01 08:00:00'),
(2, 'SPORTS15', 'Fitness Week', 'percent', 15.00, 8000.00, 200, 46, '2026-12-31 23:59:59', 1, '2026-01-05 08:00:00'),
(3, 'HOME500', 'Kitchen Festival', 'fixed', 500.00, 5000.00, 300, 74, '2026-11-30 23:59:59', 1, '2026-01-10 08:00:00'),
(4, 'BOOKLOVER', 'Readers Month', 'percent', 12.00, 1500.00, 250, 32, '2026-10-31 23:59:59', 1, '2026-01-15 08:00:00'),
(5, 'BEAUTY8', 'Skincare Promo', 'percent', 8.00, 2500.00, 220, 27, '2026-09-30 23:59:59', 1, '2026-01-20 08:00:00'),
(6, 'FLASH300', 'Flash Sale', 'fixed', 300.00, 2000.00, 180, 19, '2026-08-31 23:59:59', 1, '2026-01-25 08:00:00'),
(7, 'AUTO200', 'Auto Care Deal', 'fixed', 200.00, 3000.00, 150, 11, '2026-12-15 23:59:59', 1, '2026-02-01 08:00:00'),
(8, 'HEALTH5', 'Wellness Drive', 'percent', 5.00, 4000.00, 500, 24, '2026-12-01 23:59:59', 1, '2026-02-05 08:00:00'),
(9, 'MEGA20', 'Mega Campaign', 'percent', 20.00, 15000.00, 80, 9, '2026-07-31 23:59:59', 1, '2026-02-10 08:00:00'),
(10, 'OLDCODE', 'Expired Legacy', 'percent', 10.00, 1000.00, 50, 50, '2025-12-31 23:59:59', 0, '2025-01-01 08:00:00')
ON DUPLICATE KEY UPDATE
code = VALUES(code),
campaign_name = VALUES(campaign_name),
discount_type = VALUES(discount_type),
discount_value = VALUES(discount_value),
min_order_value = VALUES(min_order_value),
max_uses = VALUES(max_uses),
uses_count = VALUES(uses_count),
expires_at = VALUES(expires_at),
is_active = VALUES(is_active);

INSERT INTO addresses
(id, user_id, label, full_name, phone, address_line, city, province, postal_code, country, is_default, created_at)
VALUES
(1, 6, 'Home', 'Ali Khan', '+923001000006', 'House 21, Street 4, DHA', 'Lahore', 'Punjab', '54000', 'Pakistan', 1, '2026-02-02 09:00:00'),
(2, 7, 'Home', 'Aisha Noor', '+923001000007', 'Flat 9, Block B, Gulberg', 'Lahore', 'Punjab', '54660', 'Pakistan', 1, '2026-02-02 09:05:00'),
(3, 8, 'Home', 'Zain Ahmed', '+923001000008', 'House 15, F-10', 'Islamabad', 'ICT', '44000', 'Pakistan', 1, '2026-02-02 09:10:00'),
(4, 9, 'Home', 'Maryam Ali', '+923001000009', 'Apartment 3C, Clifton', 'Karachi', 'Sindh', '75600', 'Pakistan', 1, '2026-02-02 09:15:00'),
(5, 10, 'Home', 'Hassan Raza', '+923001000010', 'Street 11, Model Town', 'Lahore', 'Punjab', '54700', 'Pakistan', 1, '2026-02-02 09:20:00'),
(6, 11, 'Home', 'Noor Fatima', '+923001000011', 'House 7, Satellite Town', 'Rawalpindi', 'Punjab', '46000', 'Pakistan', 1, '2026-02-02 09:25:00'),
(7, 12, 'Home', 'Usman Saeed', '+923001000012', 'Phase 2, Bahria Town', 'Rawalpindi', 'Punjab', '46220', 'Pakistan', 1, '2026-02-02 09:30:00'),
(8, 13, 'Home', 'Komal Sheikh', '+923001000013', 'Street 14, Johar Town', 'Lahore', 'Punjab', '54782', 'Pakistan', 1, '2026-02-02 09:35:00'),
(9, 14, 'Home', 'Faizan Habib', '+923001000014', 'House 80, PECHS', 'Karachi', 'Sindh', '75400', 'Pakistan', 1, '2026-02-02 09:40:00'),
(10, 15, 'Home', 'Maham Asif', '+923001000015', 'Street 3, G-13', 'Islamabad', 'ICT', '44300', 'Pakistan', 1, '2026-02-02 09:45:00'),
(11, 6, 'Office', 'Ali Khan', '+923001000006', 'Office 22, MM Alam Road', 'Lahore', 'Punjab', '54650', 'Pakistan', 0, '2026-02-02 09:50:00'),
(12, 7, 'Office', 'Aisha Noor', '+923001000007', 'Suite 5, Main Boulevard', 'Lahore', 'Punjab', '54661', 'Pakistan', 0, '2026-02-02 09:55:00')
ON DUPLICATE KEY UPDATE
user_id = VALUES(user_id),
label = VALUES(label),
full_name = VALUES(full_name),
phone = VALUES(phone),
address_line = VALUES(address_line),
city = VALUES(city),
province = VALUES(province),
postal_code = VALUES(postal_code),
country = VALUES(country),
is_default = VALUES(is_default);

INSERT INTO orders
(id, customer_id, total_amount, status, shipping_name, shipping_address, shipping_phone, notes, discount_code_id, discount_amount, payment_method, payment_status, payment_ref, created_at, updated_at)
VALUES
(1, 6, 31000.00, 'delivered', 'Ali Khan', 'House 21, Street 4, DHA, Lahore', '+923001000006', 'Please call before delivery.', 1, 1000.00, 'card', 'paid', 'REF-ORD-1001', '2026-02-10 11:00:00', '2026-02-14 15:00:00'),
(2, 7, 18180.00, 'delivered', 'Aisha Noor', 'Flat 9, Block B, Gulberg, Lahore', '+923001000007', 'Evening delivery preferred.', 5, 500.00, 'easypaisa', 'paid', 'REF-ORD-1002', '2026-02-11 12:00:00', '2026-02-14 16:30:00'),
(3, 8, 17300.00, 'shipped', 'Zain Ahmed', 'House 15, F-10, Islamabad', '+923001000008', NULL, 2, 700.00, 'jazzcash', 'paid', 'REF-ORD-1003', '2026-02-12 13:00:00', '2026-02-15 13:00:00'),
(4, 9, 45799.00, 'processing', 'Maryam Ali', 'Apartment 3C, Clifton, Karachi', '+923001000009', 'Fragile items, handle with care.', 3, 500.00, 'card', 'paid', 'REF-ORD-1004', '2026-02-13 14:00:00', '2026-02-15 18:00:00'),
(5, 10, 4700.00, 'delivered', 'Hassan Raza', 'Street 11, Model Town, Lahore', '+923001000010', NULL, 4, 300.00, 'cod', 'paid', 'REF-ORD-1005', '2026-02-14 15:00:00', '2026-02-17 19:00:00'),
(6, 11, 24300.00, 'pending', 'Noor Fatima', 'House 7, Satellite Town, Rawalpindi', '+923001000011', 'Gift wrap requested.', NULL, 0.00, 'cod', 'pending', NULL, '2026-02-15 16:00:00', '2026-02-15 16:00:00'),
(7, 12, 12880.00, 'shipped', 'Usman Saeed', 'Phase 2, Bahria Town, Rawalpindi', '+923001000012', NULL, 6, 300.00, 'bank_transfer', 'paid', 'REF-ORD-1007', '2026-02-16 17:00:00', '2026-02-18 10:00:00'),
(8, 13, 14190.00, 'delivered', 'Komal Sheikh', 'Street 14, Johar Town, Lahore', '+923001000013', 'Deliver to reception desk.', 1, 800.00, 'card', 'paid', 'REF-ORD-1008', '2026-02-17 18:00:00', '2026-02-20 12:00:00'),
(9, 14, 13100.00, 'cancelled', 'Faizan Habib', 'House 80, PECHS, Karachi', '+923001000014', 'Customer requested cancellation.', NULL, 0.00, 'card', 'refunded', 'REF-ORD-1009', '2026-02-18 19:00:00', '2026-02-18 22:00:00'),
(10, 15, 42899.00, 'processing', 'Maham Asif', 'Street 3, G-13, Islamabad', '+923001000015', NULL, 9, 2000.00, 'card', 'pending', 'REF-ORD-1010', '2026-02-19 20:00:00', '2026-02-20 09:00:00'),
(11, 6, 45799.00, 'delivered', 'Ali Khan', 'House 21, Street 4, DHA, Lahore', '+923001000006', NULL, 3, 500.00, 'easypaisa', 'paid', 'REF-ORD-1011', '2026-02-20 10:00:00', '2026-02-23 11:00:00'),
(12, 7, 43500.00, 'pending', 'Aisha Noor', 'Flat 9, Block B, Gulberg, Lahore', '+923001000007', 'Keep package sealed.', 1, 1000.00, 'cod', 'pending', NULL, '2026-02-21 11:00:00', '2026-02-21 11:00:00')
ON DUPLICATE KEY UPDATE
customer_id = VALUES(customer_id),
total_amount = VALUES(total_amount),
status = VALUES(status),
shipping_name = VALUES(shipping_name),
shipping_address = VALUES(shipping_address),
shipping_phone = VALUES(shipping_phone),
notes = VALUES(notes),
discount_code_id = VALUES(discount_code_id),
discount_amount = VALUES(discount_amount),
payment_method = VALUES(payment_method),
payment_status = VALUES(payment_status),
payment_ref = VALUES(payment_ref),
updated_at = VALUES(updated_at);

INSERT INTO order_items
(id, order_id, product_id, retailer_id, product_name, quantity, unit_price, discount_per_unit)
VALUES
(1, 1, 1, 1, 'Noise Cancelling Headphones', 1, 18500.00, 500.00),
(2, 1, 18, 1, 'Bluetooth Portable Speaker', 1, 12500.00, 500.00),
(3, 2, 5, 2, 'Women Linen Kurta', 2, 7490.00, 250.00),
(4, 2, 12, 2, 'Vitamin C Serum', 1, 3200.00, 0.00),
(5, 3, 7, 4, 'Running Shoes Pro', 1, 12800.00, 300.00),
(6, 3, 17, 4, 'Resistance Band Set', 1, 4500.00, 400.00),
(7, 4, 9, 3, 'Air Fryer 6L', 1, 29999.00, 500.00),
(8, 4, 10, 3, 'Nonstick Cookware Set', 1, 15800.00, 0.00),
(9, 5, 15, 5, 'Productivity Planner 2026', 1, 1800.00, 100.00),
(10, 5, 16, 5, 'Python for Data Analysis', 1, 2900.00, 200.00),
(11, 6, 3, 1, 'Mechanical Keyboard', 1, 16500.00, 0.00),
(12, 6, 14, 1, 'Car Vacuum Cleaner', 1, 7800.00, 0.00),
(13, 7, 11, 3, 'Organic Green Tea 100g', 2, 1450.00, 100.00),
(14, 7, 20, 3, 'Protein Peanut Butter', 2, 4990.00, 50.00),
(15, 8, 13, 1, 'STEM Robot Kit', 1, 11200.00, 500.00),
(16, 8, 8, 4, 'Yoga Mat Anti-Slip', 1, 2990.00, 300.00),
(17, 9, 6, 2, 'Men Denim Jacket', 1, 8900.00, 0.00),
(18, 9, 19, 3, 'Ceramic Indoor Planter', 2, 2100.00, 0.00),
(19, 10, 2, 1, '4K Action Camera', 1, 27999.00, 1000.00),
(20, 10, 4, 1, 'Smart Fitness Watch', 1, 14900.00, 1000.00),
(21, 11, 10, 3, 'Nonstick Cookware Set', 1, 15800.00, 500.00),
(22, 11, 9, 3, 'Air Fryer 6L', 1, 29999.00, 0.00),
(23, 12, 18, 1, 'Bluetooth Portable Speaker', 2, 12500.00, 500.00),
(24, 12, 1, 1, 'Noise Cancelling Headphones', 1, 18500.00, 0.00)
ON DUPLICATE KEY UPDATE
order_id = VALUES(order_id),
product_id = VALUES(product_id),
retailer_id = VALUES(retailer_id),
product_name = VALUES(product_name),
quantity = VALUES(quantity),
unit_price = VALUES(unit_price),
discount_per_unit = VALUES(discount_per_unit);

INSERT INTO cart
(id, user_id, product_id, quantity, added_at, updated_at)
VALUES
(1, 6, 2, 1, '2026-02-22 09:00:00', '2026-02-22 09:00:00'),
(2, 7, 7, 1, '2026-02-22 09:05:00', '2026-02-22 09:05:00'),
(3, 8, 15, 2, '2026-02-22 09:10:00', '2026-02-22 09:10:00'),
(4, 9, 1, 1, '2026-02-22 09:15:00', '2026-02-22 09:15:00'),
(5, 10, 12, 1, '2026-02-22 09:20:00', '2026-02-22 09:20:00'),
(6, 11, 9, 1, '2026-02-22 09:25:00', '2026-02-22 09:25:00'),
(7, 12, 18, 1, '2026-02-22 09:30:00', '2026-02-22 09:30:00'),
(8, 13, 5, 1, '2026-02-22 09:35:00', '2026-02-22 09:35:00'),
(9, 14, 20, 1, '2026-02-22 09:40:00', '2026-02-22 09:40:00'),
(10, 15, 3, 1, '2026-02-22 09:45:00', '2026-02-22 09:45:00')
ON DUPLICATE KEY UPDATE
quantity = VALUES(quantity),
updated_at = VALUES(updated_at);

INSERT INTO wishlists
(id, user_id, product_id, priority, target_price, notify_on_price_drop, note, added_at)
VALUES
(1, 6, 4, 2, 13000.00, 1, 'Waiting for a sports campaign discount.', '2026-02-22 10:00:00'),
(2, 6, 13, 4, 10000.00, 1, 'Buying for nephew birthday.', '2026-02-22 10:01:00'),
(3, 7, 6, 3, 8200.00, 1, 'Prefer medium size stock.', '2026-02-22 10:02:00'),
(4, 7, 12, 1, 2900.00, 0, 'Will buy next week.', '2026-02-22 10:03:00'),
(5, 8, 2, 2, 25000.00, 1, 'Need before trip.', '2026-02-22 10:04:00'),
(6, 9, 19, 5, 1800.00, 1, 'Looking for bundle offer.', '2026-02-22 10:05:00'),
(7, 10, 16, 1, 2600.00, 0, 'Recommended by friend.', '2026-02-22 10:06:00'),
(8, 11, 20, 2, 4500.00, 1, 'If price drops under 4500.', '2026-02-22 10:07:00'),
(9, 12, 11, 4, 1200.00, 1, 'Buying in monthly groceries.', '2026-02-22 10:08:00'),
(10, 13, 15, 3, 1600.00, 0, 'Planner for exam prep.', '2026-02-22 10:09:00'),
(11, 14, 14, 1, 7000.00, 1, 'Need for car cleaning kit.', '2026-02-22 10:10:00'),
(12, 15, 1, 2, 17000.00, 1, 'Waiting for monthly payday.', '2026-02-22 10:11:00')
ON DUPLICATE KEY UPDATE
priority = VALUES(priority),
target_price = VALUES(target_price),
notify_on_price_drop = VALUES(notify_on_price_drop),
note = VALUES(note);

INSERT INTO reviews
(id, product_id, user_id, rating, title, comment, is_verified_purchase, created_at)
VALUES
(1, 1, 6, 5, 'Excellent sound quality', 'Battery life and ANC are both impressive.', 1, '2026-02-15 10:00:00'),
(2, 18, 6, 4, 'Great bass', 'Very portable and loud enough for small rooms.', 1, '2026-02-15 10:05:00'),
(3, 5, 7, 5, 'Perfect fit', 'Fabric quality is better than expected.', 1, '2026-02-16 11:00:00'),
(4, 12, 7, 4, 'Visible glow', 'Good serum for daily use.', 1, '2026-02-16 11:05:00'),
(5, 7, 8, 5, 'Comfortable run', 'Used for 10km runs, no issues.', 1, '2026-02-17 12:00:00'),
(6, 17, 8, 4, 'Good resistance', 'Useful variety of tension levels.', 1, '2026-02-17 12:05:00'),
(7, 9, 9, 4, 'Cooks evenly', 'Large capacity and easy cleanup.', 1, '2026-02-18 13:00:00'),
(8, 10, 9, 5, 'Premium quality', 'Non-stick coating is excellent.', 1, '2026-02-18 13:05:00'),
(9, 15, 10, 5, 'Very practical', 'Helped me organize semester goals.', 1, '2026-02-19 14:00:00'),
(10, 16, 10, 5, 'Great learning book', 'Concepts are clear and practical.', 1, '2026-02-19 14:05:00'),
(11, 11, 12, 4, 'Fresh aroma', 'Tastes clean and natural.', 1, '2026-02-20 15:00:00'),
(12, 20, 12, 4, 'Healthy snack', 'Good protein and texture.', 1, '2026-02-20 15:05:00'),
(13, 13, 13, 5, 'Kids loved it', 'Fun and educational at the same time.', 1, '2026-02-21 16:00:00'),
(14, 19, 14, 4, 'Looks elegant', 'Fits perfectly on office desk.', 0, '2026-02-21 16:05:00'),
(15, 6, 14, 3, 'Good but size issue', 'Quality is good, size was slightly tight.', 0, '2026-02-21 16:10:00')
ON DUPLICATE KEY UPDATE
rating = VALUES(rating),
title = VALUES(title),
comment = VALUES(comment),
is_verified_purchase = VALUES(is_verified_purchase);

INSERT INTO ai_chat_history
(id, user_id, role, sender, message, created_at)
VALUES
(1, 6, 'customer', 'user', 'Show me top electronics under Rs 20000.', '2026-02-15 10:30:00'),
(2, 6, 'customer', 'bot', 'Top picks: Noise Cancelling Headphones and Bluetooth Portable Speaker based on price and ratings.', '2026-02-15 10:30:20'),
(3, 7, 'customer', 'user', 'Any skincare items with good ratings?', '2026-02-16 11:20:00'),
(4, 7, 'customer', 'bot', 'Vitamin C Serum is currently in stock with strong user feedback.', '2026-02-16 11:20:18'),
(5, 1, 'retailer', 'user', 'How many pending orders do I have?', '2026-02-17 09:10:00'),
(6, 1, 'retailer', 'bot', 'You currently have pending and processing orders tied to your products in recent days.', '2026-02-17 09:10:22'),
(7, 3, 'retailer', 'user', 'Which products are low stock?', '2026-02-18 08:45:00'),
(8, 3, 'retailer', 'bot', 'Air Fryer 6L and Nonstick Cookware Set are below your healthy stock threshold.', '2026-02-18 08:45:17'),
(9, 10, 'customer', 'user', 'Recommend books for data analytics.', '2026-02-19 14:30:00'),
(10, 10, 'customer', 'bot', 'Python for Data Analysis and Productivity Planner 2026 are relevant options.', '2026-02-19 14:30:19')
ON DUPLICATE KEY UPDATE
user_id = VALUES(user_id),
role = VALUES(role),
sender = VALUES(sender),
message = VALUES(message),
created_at = VALUES(created_at);

INSERT INTO notifications
(id, user_id, message, type, channel, action_url, is_read, created_at)
VALUES
(1, 6, 'Your order #1 has been delivered successfully.', 'order', 'in_app', '/orders', 1, '2026-02-14 16:00:00'),
(2, 7, 'Your order #2 has been delivered successfully.', 'order', 'in_app', '/orders', 1, '2026-02-14 17:00:00'),
(3, 8, 'Order #3 has been shipped and is on the way.', 'order', 'sms', '/orders', 0, '2026-02-15 14:00:00'),
(4, 9, 'Kitchen Festival discount is active on selected items.', 'promo', 'email', '/shop?category=3', 0, '2026-02-15 18:30:00'),
(5, 10, 'Readers Month deal: extra discounts on books.', 'promo', 'email', '/shop?category=5', 0, '2026-02-16 09:00:00'),
(6, 11, 'Order #6 is pending confirmation.', 'order', 'in_app', '/orders', 0, '2026-02-16 16:05:00'),
(7, 12, 'Order #7 has been shipped.', 'order', 'sms', '/orders', 0, '2026-02-18 10:10:00'),
(8, 13, 'Order #8 delivered. Please leave a review.', 'order', 'in_app', '/orders', 0, '2026-02-20 12:30:00'),
(9, 14, 'Order #9 was cancelled and payment refunded.', 'order', 'email', '/orders', 1, '2026-02-18 22:15:00'),
(10, 15, 'Mega Campaign code is expiring soon.', 'promo', 'in_app', '/shop', 0, '2026-02-20 09:10:00'),
(11, 6, 'Price drop alert: Smart Fitness Watch is now discounted.', 'promo', 'in_app', '/product/4', 0, '2026-02-21 08:00:00'),
(12, 7, 'Wishlist item Vitamin C Serum is back in stock.', 'info', 'in_app', '/wishlist', 0, '2026-02-21 08:10:00')
ON DUPLICATE KEY UPDATE
message = VALUES(message),
type = VALUES(type),
channel = VALUES(channel),
action_url = VALUES(action_url),
is_read = VALUES(is_read);

INSERT INTO payments
(id, order_id, payment_method, provider, amount_paid, payment_status, transaction_ref, paid_at, created_at)
VALUES
(1, 1, 'card', 'HBL', 31000.00, 'paid', 'TXN-100001', '2026-02-10 11:05:00', '2026-02-10 11:05:00'),
(2, 2, 'easypaisa', 'Easypaisa', 18180.00, 'paid', 'TXN-100002', '2026-02-11 12:05:00', '2026-02-11 12:05:00'),
(3, 3, 'jazzcash', 'JazzCash', 17300.00, 'paid', 'TXN-100003', '2026-02-12 13:05:00', '2026-02-12 13:05:00'),
(4, 4, 'card', 'UBL', 45799.00, 'paid', 'TXN-100004', '2026-02-13 14:05:00', '2026-02-13 14:05:00'),
(5, 5, 'cod', 'Cash', 4700.00, 'paid', 'TXN-100005', '2026-02-17 19:10:00', '2026-02-17 19:10:00'),
(6, 6, 'cod', 'Cash', 0.00, 'pending', NULL, NULL, '2026-02-15 16:05:00'),
(7, 7, 'bank_transfer', 'Meezan', 12880.00, 'paid', 'TXN-100007', '2026-02-16 17:10:00', '2026-02-16 17:10:00'),
(8, 8, 'card', 'MCB', 14190.00, 'paid', 'TXN-100008', '2026-02-17 18:10:00', '2026-02-17 18:10:00'),
(9, 9, 'card', 'Alfalah', 13100.00, 'refunded', 'TXN-100009', '2026-02-18 19:05:00', '2026-02-18 19:05:00'),
(10, 10, 'card', 'HBL', 0.00, 'pending', 'TXN-100010', NULL, '2026-02-19 20:05:00'),
(11, 11, 'easypaisa', 'Easypaisa', 45799.00, 'paid', 'TXN-100011', '2026-02-20 10:10:00', '2026-02-20 10:10:00'),
(12, 12, 'cod', 'Cash', 0.00, 'pending', NULL, NULL, '2026-02-21 11:05:00')
ON DUPLICATE KEY UPDATE
payment_method = VALUES(payment_method),
provider = VALUES(provider),
amount_paid = VALUES(amount_paid),
payment_status = VALUES(payment_status),
transaction_ref = VALUES(transaction_ref),
paid_at = VALUES(paid_at);

INSERT INTO shipments
(id, order_id, courier_name, tracking_number, shipment_status, shipped_at, expected_delivery, delivered_at, shipping_cost, created_at)
VALUES
(1, 1, 'TCS', 'TRK-TCS-1001', 'delivered', '2026-02-11 09:00:00', '2026-02-14 18:00:00', '2026-02-14 15:00:00', 250.00, '2026-02-11 09:00:00'),
(2, 2, 'Leopards', 'TRK-LEO-1002', 'delivered', '2026-02-12 09:30:00', '2026-02-14 20:00:00', '2026-02-14 16:30:00', 220.00, '2026-02-12 09:30:00'),
(3, 3, 'BlueEx', 'TRK-BLX-1003', 'in_transit', '2026-02-13 10:00:00', '2026-02-16 18:00:00', NULL, 230.00, '2026-02-13 10:00:00'),
(4, 4, 'MNP', 'TRK-MNP-1004', 'packed', '2026-02-15 11:00:00', '2026-02-18 18:00:00', NULL, 260.00, '2026-02-15 11:00:00'),
(5, 5, 'TCS', 'TRK-TCS-1005', 'delivered', '2026-02-15 12:00:00', '2026-02-17 18:00:00', '2026-02-17 19:00:00', 180.00, '2026-02-15 12:00:00'),
(6, 6, 'Leopards', 'TRK-LEO-1006', 'pending', NULL, NULL, NULL, 200.00, '2026-02-15 16:10:00'),
(7, 7, 'TCS', 'TRK-TCS-1007', 'in_transit', '2026-02-17 09:00:00', '2026-02-20 18:00:00', NULL, 210.00, '2026-02-17 09:00:00'),
(8, 8, 'MNP', 'TRK-MNP-1008', 'delivered', '2026-02-18 09:00:00', '2026-02-20 18:00:00', '2026-02-20 12:00:00', 240.00, '2026-02-18 09:00:00'),
(9, 10, 'BlueEx', 'TRK-BLX-1010', 'packed', '2026-02-20 11:00:00', '2026-02-23 18:00:00', NULL, 270.00, '2026-02-20 11:00:00'),
(10, 11, 'Leopards', 'TRK-LEO-1011', 'delivered', '2026-02-21 11:00:00', '2026-02-23 18:00:00', '2026-02-23 11:00:00', 260.00, '2026-02-21 11:00:00')
ON DUPLICATE KEY UPDATE
courier_name = VALUES(courier_name),
tracking_number = VALUES(tracking_number),
shipment_status = VALUES(shipment_status),
shipped_at = VALUES(shipped_at),
expected_delivery = VALUES(expected_delivery),
delivered_at = VALUES(delivered_at),
shipping_cost = VALUES(shipping_cost);

-- ============================================================
-- 3) QUICK VALIDATION QUERIES (Run manually after import)
-- ============================================================
-- SELECT 'users' AS table_name, COUNT(*) AS rows_count FROM users
-- UNION ALL SELECT 'categories', COUNT(*) FROM categories
-- UNION ALL SELECT 'products', COUNT(*) FROM products
-- UNION ALL SELECT 'product_images', COUNT(*) FROM product_images
-- UNION ALL SELECT 'discount_codes', COUNT(*) FROM discount_codes
-- UNION ALL SELECT 'addresses', COUNT(*) FROM addresses
-- UNION ALL SELECT 'orders', COUNT(*) FROM orders
-- UNION ALL SELECT 'order_items', COUNT(*) FROM order_items
-- UNION ALL SELECT 'cart', COUNT(*) FROM cart
-- UNION ALL SELECT 'wishlists', COUNT(*) FROM wishlists
-- UNION ALL SELECT 'reviews', COUNT(*) FROM reviews
-- UNION ALL SELECT 'ai_chat_history', COUNT(*) FROM ai_chat_history
-- UNION ALL SELECT 'notifications', COUNT(*) FROM notifications
-- UNION ALL SELECT 'payments', COUNT(*) FROM payments
-- UNION ALL SELECT 'shipments', COUNT(*) FROM shipments;

-- Example multi-table sanity check
-- SELECT o.id AS order_id, u.username AS customer, o.status,
--        SUM(oi.quantity * (oi.unit_price - oi.discount_per_unit)) AS computed_total
-- FROM orders o
-- JOIN users u ON u.id = o.customer_id
-- JOIN order_items oi ON oi.order_id = o.id
-- GROUP BY o.id, u.username, o.status
-- ORDER BY o.id;
