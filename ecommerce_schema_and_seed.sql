-- E-commerce store database schema + seed data (SQLite-compatible SQL)
-- Core entities: Customers, Categories, Products
-- Relationships:
-- - An order is placed by a customer
-- - An order contains one or more order_items (a product + quantity + unit price)
-- - Each product belongs to a category

PRAGMA foreign_keys = ON;

-- Drop in dependency order (safe for reruns)
DROP TABLE IF EXISTS order_items;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
  customer_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  country TEXT,
  signup_date TEXT NOT NULL
);

CREATE TABLE categories (
  category_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE products (
  product_id INTEGER PRIMARY KEY,
  sku TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  category_id INTEGER NOT NULL,
  price REAL NOT NULL CHECK (price >= 0),
  FOREIGN KEY (category_id) REFERENCES categories(category_id) ON DELETE RESTRICT
);

-- A customer order. status is constrained to a fixed lifecycle.
CREATE TABLE orders (
  order_id INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL,
  order_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','paid','shipped','delivered','cancelled')),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
);

-- Line items. unit_price is captured at purchase time (may differ from current price).
-- A product appears at most once per order (UNIQUE), with a positive quantity.
CREATE TABLE order_items (
  order_item_id INTEGER PRIMARY KEY,
  order_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  quantity INTEGER NOT NULL CHECK (quantity > 0),
  unit_price REAL NOT NULL CHECK (unit_price >= 0),
  FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE,
  FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE RESTRICT,
  UNIQUE(order_id, product_id)
);

-- Seed: Customers
INSERT INTO customers (customer_id, name, email, country, signup_date) VALUES
  (1, 'Alice Cohen',   'alice@example.com', 'Israel',   '2024-01-15'),
  (2, 'Ben Levi',      'ben@example.com',   'Israel',   '2024-03-02'),
  (3, 'Carla Mendes',  'carla@example.com', 'Portugal', '2025-02-20'),
  (4, 'David Kim',     'david@example.com', 'USA',      '2025-05-10'),
  (5, 'Emma Schmidt',  'emma@example.com',  'Germany',  '2025-06-01');

-- Seed: Categories
INSERT INTO categories (category_id, name) VALUES
  (1, 'Electronics'),
  (2, 'Books'),
  (3, 'Home & Kitchen'),
  (4, 'Toys');

-- Seed: Products
INSERT INTO products (product_id, sku, name, category_id, price) VALUES
  (1,  'ELEC-001', 'Wireless Headphones',  1, 120.00),
  (2,  'ELEC-002', 'USB-C Charger',        1,  25.00),
  (3,  'ELEC-003', 'Bluetooth Speaker',    1,  80.00),
  (4,  'BOOK-001', 'SQL for Beginners',    2,  30.00),
  (5,  'BOOK-002', 'Python Crash Course',  2,  40.00),
  (6,  'HOME-001', 'Coffee Mug',           3,  12.00),
  (7,  'HOME-002', 'Chef Knife',           3,  55.00),
  (8,  'TOY-001',  'Building Blocks Set',  4,  35.00),
  (9,  'TOY-002',  'Remote Control Car',   4,  60.00),
  (10, 'ELEC-004', 'Webcam HD',            1,  45.00);

-- Seed: Orders
INSERT INTO orders (order_id, customer_id, order_date, status) VALUES
  (1, 1, '2025-01-10', 'delivered'),
  (2, 1, '2025-03-15', 'delivered'),
  (3, 2, '2025-02-01', 'shipped'),
  (4, 3, '2025-02-25', 'delivered'),
  (5, 4, '2025-05-12', 'paid'),
  (6, 5, '2025-06-02', 'pending'),
  (7, 2, '2025-04-20', 'cancelled'),
  (8, 1, '2025-05-30', 'delivered');

-- Seed: Order items
INSERT INTO order_items (order_id, product_id, quantity, unit_price) VALUES
  (1, 1, 1, 120.00),   -- Alice: Wireless Headphones
  (1, 4, 2,  30.00),   -- Alice: SQL for Beginners x2
  (2, 6, 3,  12.00),   -- Alice: Coffee Mug x3
  (2, 2, 1,  25.00),   -- Alice: USB-C Charger
  (3, 3, 1,  80.00),   -- Ben:   Bluetooth Speaker
  (4, 5, 1,  40.00),   -- Carla: Python Crash Course
  (4, 4, 1,  30.00),   -- Carla: SQL for Beginners
  (5, 9, 2,  60.00),   -- David: Remote Control Car x2
  (6, 8, 1,  35.00),   -- Emma:  Building Blocks Set
  (7, 7, 1,  55.00),   -- Ben:   Chef Knife (cancelled order)
  (8, 1, 1, 120.00),   -- Alice: Wireless Headphones
  (8, 10, 1, 45.00);   -- Alice: Webcam HD
