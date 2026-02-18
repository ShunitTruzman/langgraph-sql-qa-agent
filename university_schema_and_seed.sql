-- University database schema + seed data (SQLite-compatible SQL)
-- Core entities: Teachers, Students, Courses
-- Relationships:
-- - Course offering (course taught by a teacher in a semester/year)
-- - Enrollment (student enrolls in offering)
-- - Grade stored on enrollment

PRAGMA foreign_keys = ON;

-- Drop in dependency order (safe for reruns)
DROP TABLE IF EXISTS enrollments;
DROP TABLE IF EXISTS course_offerings;
DROP TABLE IF EXISTS courses;
DROP TABLE IF EXISTS students;
DROP TABLE IF EXISTS teachers;

CREATE TABLE teachers (
  teacher_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE students (
  student_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  major TEXT
);

CREATE TABLE courses (
  course_id INTEGER PRIMARY KEY,
  code TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  credits INTEGER NOT NULL CHECK (credits > 0)
);

-- A specific instance of a course taught by a teacher in a semester/year.
CREATE TABLE course_offerings (
  offering_id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL,
  teacher_id INTEGER NOT NULL,
  semester TEXT NOT NULL CHECK (semester IN ('Spring','Summer','Fall','Winter')),
  year INTEGER NOT NULL CHECK (year >= 2000),
  section TEXT NOT NULL DEFAULT 'A',
  FOREIGN KEY (course_id) REFERENCES courses(course_id) ON DELETE RESTRICT,
  FOREIGN KEY (teacher_id) REFERENCES teachers(teacher_id) ON DELETE RESTRICT,
  UNIQUE(course_id, teacher_id, semester, year, section)
);

-- Enrollment also stores the student's numeric grade (0-100) for that offering.
CREATE TABLE enrollments (
  enrollment_id INTEGER PRIMARY KEY,
  student_id INTEGER NOT NULL,
  offering_id INTEGER NOT NULL,
  grade REAL CHECK (grade IS NULL OR (grade >= 0 AND grade <= 100)),
  FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
  FOREIGN KEY (offering_id) REFERENCES course_offerings(offering_id) ON DELETE CASCADE,
  UNIQUE(student_id, offering_id)
);

-- Seed: Teachers
INSERT INTO teachers (teacher_id, name) VALUES
  (1, 'Dr. Alice Nguyen'),
  (2, 'Dr. Ben Carter'),
  (3, 'Prof. Carla Singh');

-- Seed: Students
INSERT INTO students (student_id, name, major) VALUES
  (1, 'Maya Patel', 'Computer Science'),
  (2, 'Noah Kim', 'Mathematics'),
  (3, 'Ava Lopez', 'History'),
  (4, 'Ethan Zhang', 'Computer Science'),
  (5, 'Sophia Rossi', 'Business');

-- Seed: Courses
INSERT INTO courses (course_id, code, title, credits) VALUES
  (1, 'CS101', 'Intro to Computer Science', 4),
  (2, 'CS201', 'Data Structures', 4),
  (3, 'MATH201', 'Linear Algebra', 3),
  (4, 'HIST110', 'World History', 3),
  (5, 'BUS150', 'Principles of Management', 3);

-- Seed: Course offerings (semester/year)
INSERT INTO course_offerings (offering_id, course_id, teacher_id, semester, year, section) VALUES
  (1, 1, 1, 'Fall',   2025, 'A'),  -- CS101 by Alice
  (2, 2, 2, 'Fall',   2025, 'A'),  -- CS201 by Ben
  (3, 3, 3, 'Fall',   2025, 'A'),  -- MATH201 by Carla
  (4, 4, 3, 'Spring', 2026, 'A'),  -- HIST110 by Carla
  (5, 1, 1, 'Spring', 2026, 'A'),  -- CS101 by Alice
  (6, 5, 2, 'Spring', 2026, 'A');  -- BUS150 by Ben

-- Seed: Enrollments + grades
-- Fall 2025
INSERT INTO enrollments (enrollment_id, student_id, offering_id, grade) VALUES
  (1, 1, 1, 92),  -- Maya in CS101 (Fall 2025)
  (2, 4, 1, 85),  -- Ethan in CS101 (Fall 2025)
  (3, 2, 2, 88),  -- Noah in CS201 (Fall 2025)
  (4, 1, 2, 95),  -- Maya in CS201 (Fall 2025)
  (5, 2, 3, 91),  -- Noah in MATH201 (Fall 2025)
  (6, 3, 3, 84);  -- Ava in MATH201 (Fall 2025)

-- Spring 2026
INSERT INTO enrollments (enrollment_id, student_id, offering_id, grade) VALUES
  (7,  3, 4, 89),  -- Ava in HIST110 (Spring 2026)
  (8,  5, 6, 90),  -- Sophia in BUS150 (Spring 2026)
  (9,  1, 5, 94),  -- Maya in CS101 (Spring 2026)
  (10, 2, 6, 78),  -- Noah in BUS150 (Spring 2026)
  (11, 4, 5, 87);  -- Ethan in CS101 (Spring 2026)

