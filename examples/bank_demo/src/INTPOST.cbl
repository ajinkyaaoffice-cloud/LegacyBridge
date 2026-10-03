       IDENTIFICATION DIVISION.
       PROGRAM-ID. INTPOST.
      *================================================================*
      * INTPOST - INTEREST POSTING RUN                                 *
      * INPUT (STDIN): 'P' RECORDS, ONE PER ACCOUNT PERIOD.            *
      * CALLS INTCALC FOR EACH VALID RECORD AND PRINTS A SUMMARY.      *
      *================================================================*
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT IN-FILE ASSIGN TO KEYBOARD
               ORGANIZATION IS LINE SEQUENTIAL.
       DATA DIVISION.
       FILE SECTION.
       FD  IN-FILE.
       01  IN-POST.
           05 IP-REC-TYPE          PIC X.
              88 IP-IS-POST        VALUE 'P'.
           05 IN-ACCT-ID           PIC X(8).
           05 IN-PRINCIPAL         PIC S9(9)V99
                                   SIGN LEADING SEPARATE.
           05 IN-RATE              PIC 9V9(5).
           05 IN-START-DATE        PIC 9(8).
           05 IN-END-DATE          PIC 9(8).
           05 IN-METHOD            PIC X.
              88 IN-METHOD-VALUES  VALUE 'S' 'M' 'C'.
       WORKING-STORAGE SECTION.
       COPY INTPARM.
       01  WS-EOF                  PIC X VALUE 'N'.
       01  WS-REC-NO               PIC 9(5) VALUE 0.
       01  WS-OK-COUNT             PIC 9(5) VALUE 0.
       01  WS-ERR-COUNT            PIC 9(5) VALUE 0.
       01  WS-TOTAL                PIC S9(9)V99 COMP-3 VALUE 0.
       01  WS-AVG                  PIC S9(7)V99.
       01  OUT-LINE.
           05 OL-ID                PIC X(8).
           05 FILLER               PIC X(6) VALUE ' DAYS='.
           05 OL-DAYS              PIC -ZZZZ9.
           05 FILLER               PIC X(5) VALUE ' INT='.
           05 OL-INT               PIC -Z,ZZZ,ZZ9.99.
           05 FILLER               PIC X(4) VALUE ' RC='.
           05 OL-RC                PIC 99.
       01  OUT-BAD.
           05 FILLER               PIC X(11) VALUE 'BAD RECORD '.
           05 OB-REC-NO            PIC ZZZZ9.
       01  OUT-SUMMARY.
           05 FILLER               PIC X(7) VALUE 'POSTED='.
           05 OS-COUNT             PIC ZZZZ9.
           05 FILLER               PIC X(8) VALUE ' ERRORS='.
           05 OS-ERRORS            PIC ZZZZ9.
           05 FILLER               PIC X(7) VALUE ' TOTAL='.
           05 OS-TOTAL             PIC -ZZZ,ZZZ,ZZ9.99.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT IN-FILE
           PERFORM UNTIL WS-EOF = 'Y'
               READ IN-FILE
                   AT END
                       MOVE 'Y' TO WS-EOF
                   NOT AT END
                       ADD 1 TO WS-REC-NO
                       PERFORM 1000-PROCESS
               END-READ
           END-PERFORM
           CLOSE IN-FILE
           PERFORM 2000-SUMMARY
           STOP RUN.

       1000-PROCESS.
           IF NOT IP-IS-POST OR IN-PRINCIPAL NOT NUMERIC
              OR IN-RATE NOT NUMERIC OR IN-START-DATE NOT NUMERIC
              OR IN-END-DATE NOT NUMERIC
               MOVE WS-REC-NO TO OB-REC-NO
               DISPLAY OUT-BAD
               ADD 1 TO WS-ERR-COUNT
           ELSE
               MOVE IN-PRINCIPAL TO IP-PRINCIPAL
               MOVE IN-RATE TO IP-ANNUAL-RATE
               MOVE IN-START-DATE TO IP-START-DATE
               MOVE IN-END-DATE TO IP-END-DATE
               MOVE IN-METHOD TO IP-METHOD
               MOVE 0 TO IP-DAYS
               CALL 'INTCALC' USING INT-PARMS
               MOVE IN-ACCT-ID TO OL-ID
               MOVE IP-DAYS TO OL-DAYS
               MOVE IP-INTEREST TO OL-INT
               MOVE IP-RETURN-CODE TO OL-RC
               DISPLAY OUT-LINE
               IF IP-RETURN-CODE = 0
                   ADD 1 TO WS-OK-COUNT
                   ADD IP-INTEREST TO WS-TOTAL
               ELSE
                   ADD 1 TO WS-ERR-COUNT
               END-IF
           END-IF.

       2000-SUMMARY.
           MOVE WS-OK-COUNT TO OS-COUNT
           MOVE WS-ERR-COUNT TO OS-ERRORS
           MOVE WS-TOTAL TO OS-TOTAL
           DISPLAY OUT-SUMMARY
           IF WS-OK-COUNT = 0
               DISPLAY 'NO INTEREST POSTED'
           ELSE
               DIVIDE WS-TOTAL BY WS-OK-COUNT GIVING WS-AVG ROUNDED
               DISPLAY 'AVERAGE INTEREST ' WS-AVG
               DISPLAY 'RECORDS READ ' WS-REC-NO
           END-IF.
